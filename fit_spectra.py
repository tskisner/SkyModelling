#!/usr/bin/env python
"""
This fits SpFrame spectra with airglow lines and a simple model for the continuum, 
including zodiacal light. The fit is done using a simple linear regression. 
After running the least squares fit, the fitted lines are separated from the continuum and 
the lines, continuum and residuals are returned independently.

INPUT: * SpFrame flux files as .npy files with wavelength and sky spectra 
       * Also need a list of airglow lines. these should be on the github repo

OUTPUT: numpy files identified in the same way as the sky_flux files with the plate number 
and file identifier "split_spectra". The numpy arrays have the following fields: WAVE, LINES, CONT, RESIDS

Before running, identify the directory that the spframe flux files are kept and where the airglow lines are
saved. Also identify where you want to save the files generated by this program (SAVE_DIR)

Title: SpFrame Flux Spectra Fit
Author: P. Fagrelius
Date: Mar. 21, 2017

"""
from __future__ import print_function,division
import glob, os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.linalg import lstsq
from astropy.io import fits
from scipy import interpolate
import multiprocessing
from scipy.special import eval_legendre, eval_chebys
from numpy.linalg import inv
import statsmodels.api as sm
from datetime import datetime

parallel=True
MPI=False

#Directory to save data
SAVE_DIR = os.getcwd()+'/split_flux/'
#Directory where all SpFrame flux files reside
SPECTRA_DIR = os.getcwd()+'/sigma_sky_flux/'

def main():
    # Load spectra data
    SPECTRA_FILES = os.listdir(SPECTRA_DIR)
    
    #Compare with what has already been done
    COMPLETE_FILES = os.listdir(SAVE_DIR)
    Complete_Plate_Names = []
    All_Plate_Names = []
    for d in COMPLETE_FILES:
        Complete_Plate_Names.append(d[-23:-19])
    for d in SPECTRA_FILES:
        All_Plate_Names.append(d[-23:-19])

    plates_needed_idx = [i for i, x in enumerate(All_Plate_Names) if x not in Complete_Plate_Names]
    SPECTRA = []
    for x in plates_needed_idx:
        SPECTRA.append(SPECTRA_FILES[x])
    print('Will be analyzing %d plate files' %len(SPECTRA))

    #Get meta data
    global MetaData
    MetaData = np.load('meta_rich.npy')
    print("got MetaData")
    
    #Get Airglowlines
    AIRGLOW_DIR = '/Users/parkerf/Research/SkyModel/SkyModelling/AirglowSpectra/cosby'
    AirglowFiles = glob.glob(AIRGLOW_DIR+'/*.txt')

    global AirglowLines
    AirglowLines = []
    for file in AirglowFiles:
        data = pd.read_csv(file,delim_whitespace=True)
        d = data.to_records(index=False)
        AirglowLines.append(np.array(d))
    AirglowLines = np.hstack(AirglowLines)

    

    ############
    ## SCRIPT ##
    ############
    
    if parallel:
        ## implement if MPI
        #multiprocessing speedup
        pool = multiprocessing.Pool(processes=32)
        data = pool.map(fit_and_separate_spectra, SPECTRA)
        pool.terminate()
    else:
        data = [fit_and_separate_spectra(p) for p in SPECTRA]

def get_vac_lines():
    b_sig = np.where(AirglowLines['obs_eint']>10)
    bVL = air_to_vac(AirglowLines['obs_wave'])
    bVL = bVL[b_sig] #nm to A
    blueVacLines = bVL[bVL<700]

    r_sig = np.where(AirglowLines['obs_eint']>3)
    rVL = air_to_vac(AirglowLines['obs_wave'])
    rVL = rVL[r_sig] #nm to A
    redVacLines = rVL[rVL>560]

    return blueVacLines, redVacLines

def clean_spectra(spectrum):
    """Takes out all nan/inf so lstsq will run smoothly
    """
    ok = np.isfinite(spectrum['SKY'])

    wave = spectrum['WAVE'][ok]
    sky = spectrum['SKY'][ok]
    sigma = spectrum['SIGMA'][ok]
    disp = spectrum['DISP'][ok]
    
    return [wave,sky,sigma,disp]

def air_to_vac(wave):
    """Index of refraction to go from wavelength in air to wavelength in vacuum
    Equation from (Edlen 1966)
    vac_wave = n*air_wave
    """
    #Convert to um
    wave_um = wave*.001
    ohm2 = (1./wave_um)**(2)

    #Calculate index at every wavelength
    nn = []
    for x in ohm2:
        n = 1+10**(-8)*(8342.13 + (2406030/float(130.-x)) + (15997/float(389-x)))
        nn.append(n)
    
    #Get new wavelength by multiplying by index of refraction
    vac_wave = nn*wave
    return vac_wave

def airglow_line_components(airglow_lines, wave_range, disp_range):
    """ Takes each Airglow line included in the analysis and creates a gaussian profile 
    of the line. 
    INPUT: - List of airglow lines wanted to model
           - Wavelength range of the spectra
           - Sigma for the wavelength range of the spectra
    OUTPUT: 
           Matrix with all lines used for lienar regression. Size[len(wave_range),len(airglow_lines)]
    """
    AA = []
    for line in airglow_lines:
        ss = []
        for i, w in enumerate(wave_range):
            sig = disp_range[i]
            ss.append(np.exp(-0.5*((w-line)/sig)**2))
        AA.append(ss)
    return np.vstack(AA)

def linear_model(spectrum, num_cont, airglow_lines):
    wave_range, sky_spectra, sigma_range, disp_range = clean_spectra(spectrum)

    AA = airglow_line_components(airglow_lines, wave_range, disp_range)

    # Continuum model
    AC = []
    for i in range(num_cont):
        AC.append(eval_legendre(i, wave_range))
    AC = np.array(AC)
    A = np.stack(np.vstack((AC,AA)),axis=1)

    results = sm.OLS(sky_spectra, A).fit()
    params = results.params
    model = np.dot(A, params)
    
    #R^2
    resids = sky_spectra-model
    R_1 = np.sum([(i)**2 for i in resids])
    R_2 = np.sum([(i-np.mean(sky_spectra))**2 for i in sky_spectra])  
    R = 1-(R_1/R_2)   

    #Separate
    cont = np.dot(A[:,0:num_cont],params[0:num_cont])
    lines = np.dot(A[:,num_cont:],params[num_cont:])
    res = sky_spectra - model
    
    return [wave_range, lines, cont, res, R]

def fit_and_separate_spectra(spectra_file):
    BlueVacLines, RedVacLines = get_vac_lines()

    plate_num = spectra_file[-23:-19]
    print("Fitting spectra in plate %s" %plate_num)
    spectra = np.load(SPECTRA_DIR+spectra_file)
    this_plate = MetaData[MetaData['PLATE'] == int(plate_num)]
    max_num = 10 #len(spectra) Number of spectra in a given plate that you want to run this for. Mostly for debugging
    specnos = np.random.choice(this_plate['SPECNO'], size=max_num)
    num = 0
    data = []
   
    for i, specno in enumerate(specnos):
        if num < max_num:
            start = datetime.now()
            print('splitting spectra %d/%d for plate %s' % (i,len(specnos),plate_num))
            this_obs = this_plate[this_plate['SPECNO'] == specno]
            if (this_obs['CAMERAS'] == b'b1') | (this_obs['CAMERAS'] == b'b2'):
                model = linear_model(spectra[specno], 4, BlueVacLines)
            elif (this_obs['CAMERAS'] == b'r1') | (this_obs['CAMERAS'] == b'r2'):
                model = linear_model(spectra[specno], 3, RedVacLines)
            else:
                print("Don't recognize the camera")
                model = [0,0,0,0,0]
            fit_time = (datetime.now()-start).total_seconds()
            #Save pieces
            model_fit = np.zeros(len(model[0]),dtype=[('TIME','f8'),('PLATE','i4'),('COLOR','S2'),('NUM','i4'),('WAVE','f8'),('LINES','f8'),('CONT','f8'),('RESIDS','f8'),('R','f8')])
            model_fit['TIME'] = fit_time
            model_fit['PLATE'] = plate_num
            model_fit['COLOR'] = this_obs['CAMERAS']
            model_fit['NUM'] = specno
            model_fit['WAVE'] = model[0]
            model_fit['LINES'] = model[1]
            model_fit['CONT'] = model[2]
            model_fit['RESIDS'] = model[3]
            model_fit['R'] = model[4]
            data.append(model_fit)
            num+=1
        else:
            break

    np.save(SAVE_DIR+plate_num+'_split_fit',data)

        

if __name__=="__main__":
          main()


