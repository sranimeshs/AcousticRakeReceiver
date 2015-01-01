
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from scipy.io import wavfile
from scipy.signal import resample

import Room as rg
import beamforming as bf

from constants import eps
from stft import stft, spectroplot
import windows
import utilities as u
import phat as phat
import metrics as metrics

# find all WAV files in a directory
def find_all_wav(directory):
    import fnmatch
    import os
    return [file for file in os.listdir(directory) if fnmatch.fnmatch(file, '*.WAV')]

def to_16b(signal):
    '''
    converts float 32 bit signal (-1 to 1) to a signed 16 bits representation
    '''
    return ((2**15-1)*signal).astype(np.int16)

def perceptual_quality_evaluation(n_sources, Loops):

    # Simulation parameters
    timit_dir = 'timit'
    files_dir1 = 'timit/TIMIT/TEST/DR1/FAKS0/'
    files1 = find_all_wav(files_dir1)
    files_dir2 = 'timit/TIMIT/TEST/DR1/MRJO0/'
    files2 = find_all_wav(files_dir2)

    # we the speech samples used
    speech_sample1 = files_dir1 + 'SA1.WAV'
    speech_sample2 = files_dir2 + 'SI1364.WAV'

    # Some simulation parameters
    Fs = 16000
    t0 = 1./(Fs*np.pi*1e-2)  # starting time function of sinc decay in RIR response
    absorption = 0.90
    max_order_sim = 10
    SNR_at_mic = 20          # SNR at center of microphone array in dB

    # Room 1 : Shoe box
    room_dim = [4, 6]

    # we restrict sources to be in a square 1m away from every wall and from the array
    bbox_size = [2.,2.5]
    bbox_origin = [1.,2.5]

    # microphone array design parameters
    mic1 = [2, 1.5]         # position
    M = 8                   # number of microphones
    d = 0.08                # distance between microphones
    phi = 0.                # angle from horizontal
    shape = 'Linear'        # array shape

    # create a microphone array
    if shape is 'Circular':
        mics = bf.Beamformer.circular2D(Fs, mic1, M, phi, d*M/(2*np.pi)) 
    else:
        mics = bf.Beamformer.linear2D(Fs, mic1, M, phi, d) 

    # create a single reference mic at center of array
    ref_mic = bf.MicrophoneArray(mics.center, Fs)

    # define the array processing type
    L = 4096                # frame length
    hop = 2048              # hop between frames
    zp = 2048               # zero padding (front + back)
    mics.setProcessing('FrequencyDomain', L, hop, zp, zp)

    # data receptacles
    beamformer_names = ['Rake-DS',
                        'Rake-MaxSINR',
                        'Rake-MaxUDR']
    bf_weights_fun   = [mics.rakeDelayAndSumWeights,
                        mics.rakeMaxSINRWeights,
                        mics.rakeMaxUDRWeights]
    bf_fnames = ['1','2','3']
    NBF = len(beamformer_names)

    pesq_input_rawmos = np.zeros(Loops)
    pesq_input_moslqo = np.zeros(Loops)

    pesq_rawmos = np.zeros((NBF, Loops))
    pesq_moslqo = np.zeros((NBF, Loops))

    isinr = np.zeros(Loops)
    osinr = np.zeros((NBF, Loops))

    # since we run multiple thread, we need to uniquely identify filenames
    import os
    pid = str(os.getpid())

    file_ref  = 'output_samples/fqref' + pid + '.wav'
    file_bf_base = 'output_samples/fq'
    file_bf_suffix = '-' + pid + '.wav'
    file_raw  = 'output_samples/fqraw' + pid + '.wav'

    # we need the scikits audiolab library to read the NIST format files form TIMIT
    from scikits.audiolab import Sndfile

    # Read the two speech samples used
    f = Sndfile(speech_sample1, 'r')
    good_signal = f.read_frames(f.nframes)
    good_signal = np.array(good_signal, dtype=float)
    good_signal = u.normalize(good_signal)
    good_signal = u.highpass(good_signal, Fs)
    good_len = good_signal.shape[0]/float(Fs)

    f = Sndfile(speech_sample2, 'r')
    bad_signal = f.read_frames(f.nframes)
    bad_signal = np.array(bad_signal, dtype=float)
    bad_signal = u.normalize(bad_signal)
    bad_signal = u.highpass(bad_signal, Fs)
    bad_len = bad_signal.shape[0]/float(Fs)

    # variance of good signal
    good_sigma2 = np.mean(good_signal**2)

    # normalize interference signal to have equal power with desired signal
    bad_signal *= good_sigma2/np.mean(bad_signal**2)
        
    l = 0
    for l in xrange(Loops):

        # pick good source position at random
        good_source = np.random.random(2)*bbox_size + bbox_origin
        good_distance = np.linalg.norm(mics.center[:,0] - np.array(good_source))
        
        # pick bad source position at random
        bad_source = np.random.random(2)*bbox_size + bbox_origin
        bad_distance = np.linalg.norm(mics.center[:,0] - np.array(bad_source))

        if good_len > bad_len:
            good_delay = 0
            bad_delay = (good_len - bad_len)/2.
        else:
            bad_delay = 0
            good_delay = (bad_len - good_len)/2.

        # compute the noise variance at center of array wrt good signal and SNR
        sigma2_n = good_sigma2/(4*np.pi*good_distance)**2/10**(SNR_at_mic/10)

        # create the reference room for freespace, noisless, no interference simulation
        ref_room = rg.Room.shoeBox2D(
            [0,0],
            room_dim,
            Fs,
            t0 = t0,
            max_order=0,
            absorption=absorption,
            sigma2_awgn=0.)
        ref_room.addSource(good_source, signal=good_signal, delay=good_delay)
        ref_room.addMicrophoneArray(ref_mic)
        ref_room.compute_RIR()
        ref_room.simulate()
        reference = ref_mic.signals[0]
        reference_n = u.normalize(reference)

        # save the reference desired signal
        wavfile.write(file_ref, Fs, to_16b(reference_n))

        # create the 'real' room with sources and mics
        room1 = rg.Room.shoeBox2D(
            [0,0],
            room_dim,
            Fs,
            t0 = t0,
            max_order=max_order_sim,
            absorption=absorption,
            sigma2_awgn=sigma2_n)

        # add sources to room
        room1.addSource(good_source, signal=good_signal, delay=good_delay)
        room1.addSource(bad_source, signal=bad_signal, delay=bad_delay)

        # Record first the degraded signal at reference mic (center of array)
        room1.addMicrophoneArray(ref_mic)
        room1.compute_RIR()
        room1.simulate()
        raw_n = u.normalize(u.highpass(ref_mic.signals[0], Fs))

        # save degraded reference signal
        wavfile.write(file_raw, Fs, to_16b(raw_n))

        # Now record input of microphone array
        room1.addMicrophoneArray(mics)
        room1.compute_RIR()
        room1.simulate()

        # Compute PESQ and SINR of raw degraded reference signal
        isinr[l] = metrics.snr(reference_n, raw_n[:reference_n.shape[0]])
        try:
            pesq_input_rawmos[l], pesq_input_moslqo[l] = metrics.pesq(file_ref, file_raw, Fs=Fs)
        except ValueError as ve:
            pesq_input_rawmos[l], pesq_input_moslqo[l] = np.nan, np.nan
            print "Oups, PESQ ValueError (assigning NaN):",ve
        except IndexError as ie:
            pesq_input_rawmos[l], pesq_input_moslqo[l] = np.nan, np.nan
            print "Oups, PESQ IndexError (assigning NaN):",ie

        ''' 
        BEAMFORMING PART
        '''
        # Extract image sources locations and create noise covariance matrix
        good_sources = room1.sources[0].getImages(n_nearest=nsources, 
                                                    ref_point=mics.center)
        bad_sources = room1.sources[1].getImages(n_nearest=nsources,
                                                    ref_point=mics.center)
        Rn = sigma2_n*np.eye(mics.M)

        # run for all beamformers considered
        for i, bfr in enumerate(beamformer_names):

            # compute the beamforming weights
            bf_weights_fun[i](good_sources, bad_sources,
                                    R_n = sigma2_n*np.eye(mics.M), 
                                    attn=True, ff=False)

            output = mics.process()
            delay = phat.delay_estimation(reference_n, output, 4096)

            # high-pass and normalize
            output = u.normalize(u.highpass(output, Fs))

            # time-align with reference segment for error metric computation
            sig = np.zeros(reference_n.shape[0])
            if (delay >= 0):
                length = np.minimum(output.shape[0], reference_n.shape[0]-delay)
                sig[delay:length+delay] = output[:length]
            else:
                length = np.minimum(output.shape[0]+delay, reference_n.shape[0])
                sig = np.zeros(reference_n.shape)
                sig[:length] = output[-delay:-delay+length]

            # save files for PESQ evaluation
            fname = file_bf_base + bf_fnames[i] + file_bf_suffix
            wavfile.write(fname, Fs, to_16b(sig))

            # compute PESQ
            try:
                pesq_rawmos[i,l], pesq_moslqo[i,l] = metrics.pesq(file_ref, fname, Fs=Fs)
            except ValueError as ve:
                pesq_rawmos[i,l], pesq_moslqo[i,l] = np.nan, np.nan
                print "Oups, PESQ ValueError (assigning NaN):",ve
            except IndexError as ie:
                pesq_rawmos[i,l], pesq_moslqo[i,l] = np.nan, np.nan
                print "Oups, PESQ IndexError (assigning NaN):",ie

            # compute output SINR
            osinr[i,l] = metrics.snr(reference_n, sig)

            # end of beamformers loop

        # end of simulation loop


    # save the simulation results to file
    filename = 'sim_data/quality_NSOURCES' + str(n_sources)  \
                + '_LOOPS' + str(Loops) + '.npz'
    np.savez(filename, isinr=isinr, osinr=osinr, 
            pesq_input_rawmos=pesq_input_rawmos, 
            pesq_input_moslqo=pesq_input_moslqo,
            pesq_rawmos=pesq_rawmos, pesq_moslqo=pesq_moslqo,
            bf_names=beamformer_names)

    print 'Median input SINR :',u.dB(np.median(isinr))
    print 'Median input PESQ [Raw MOS] :',np.median(pesq_input_rawmos)
    print 'Median input PESQ [MOS LQO] :',np.median(pesq_input_moslqo)
    for i, bfr in enumerate(beamformer_names):
        print bfr
        print '  median PESQ [Raw MOS]: ',np.median(pesq_rawmos[i,:])
        print '  median PESQ [MOS LQO]: ',np.median(pesq_moslqo[i,:])
        print '  median oSINR: ',u.dB(np.median(osinr[i,:]))


if __name__ == '__main__':

    import sys

    nsources = int(sys.argv[1])
    Loops = int(sys.argv[2])

    perceptual_quality_evaluation(nsources, Loops)
