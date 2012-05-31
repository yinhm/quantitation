# Functions for the individual state level model with random censoring, common
# variance and negative binomial counts for the number of states for each
# peptide.

import numpy as np
import copy

from scipy import linalg
from scipy import stats
from scipy import optimize
from scipy import special

# Exceptions

class Error(Exception):
    '''
    Base class for errors in lib.
    '''
    pass

class BisectionError(Error):
    '''
    Exception class for errors particular to bisection algorithms.
    '''

# Densities and probabilities

def dnorm(x, mu, sigmasq, log=False):
    '''
    Gaussian density parameterized by mean and variance.
    Syntax mirrors R.
    '''
    ld = -0.5*(np.log(2.*np.pi) + np.log(sigmasq)) - (x-mu)**2 / 2./ sigmasq
    if log:
        return ld
    else:
        return np.exp(ld)

def dlnorm(x, mu, sigmasq, log=False):
    '''
    Density function for log-normal, parameterized by mean and variance of
    log(x). Syntax mirrors R.
    '''
    ld = dnorm(np.log(x), mu, sigmasq, log=True) - np.log(x)
    if log:
        return ld
    else:
        return np.exp(ld)

def p_censored(x, eta_0, eta_1, log=False):
    '''
    Compute probability of intensity-based censoring.
    '''
    lp = -np.log(1. + np.exp(eta_0 + eta_1*x))
    if log:
        return lp
    else:
        return np.exp(lp)

def p_obs(x, eta_0, eta_1, log=False):
    '''
    Compute 1 - probability of intensity-based censoring.
    '''
    lp = -np.log(1. + np.exp(-eta_0 - eta_1*x))
    if log:
        return lp
    else:
        return np.exp(lp)

def dcensored(x, mu, sigmasq, eta_0, eta_1, log=False):
    '''
    Unnormalized density function for censored log-intensities.
    Integrates to p_censored.
    '''
    ld = dnorm(x, mu, sigmasq, log=True) + p_censored(x, eta_0, eta_1, log=True)
    if log:
        return ld
    else:
        return np.exp(ld)

def dobs(x, mu, sigmasq, eta_0, eta_1, log=False):
    '''
    Unnormalized density function for observed log-intensities.
    Integrates to p_obs.
    '''
    ld = dnorm(x, mu, sigmasq, log=True) + p_obs(x, eta_0, eta_1, log=True)
    if log:
        return ld
    else:
        return np.exp(ld)
        
# Useful derivatives; primarily used in mode-finding routines
def deriv_logdcensored(x, mu, sigmasq, eta_0, eta_1):
    deriv = (-1. + 1./(1. + np.exp(eta_0 + eta_1*x))) * eta_1 - (x - mu)/sigmasq
    return deriv

def deriv2_logdcensored(x, mu, sigmasq, eta_0, eta_1):
    deriv2 = (-1./sigmasq - (eta_1**2 * np.exp(eta_0 + eta_1*x) ) /
              (1. + np.exp(eta_0 + eta_1 * x))**2)
    return deriv2

def deriv3_logdcensored(x, mu, sigmasq, eta_0, eta_1):
    deriv3 = ((2. * eta_1**3 *np.exp(2.*eta_0 + 2.*eta_1*x)) /
              (1. + np.exp(eta_0 + eta_1*x))**3
              - (eta_1**3 * np.exp(eta_0 + eta_1*x)) /
              (1. + np.exp(eta_0 + eta_1*x))**2)
    return deriv3

# RNGs

def rncen(nobs, prc, pic, lmbda, r):
    '''
    Draw ncen | y, censoring probabilities, lambda, r.
    Must have nObs, prc, and pic as Numpy vectors of same length.
    r and lmbda must be scalars.
    '''
    m = nobs.size

    # Setup vectors for result and stopping indicators
    done    = np.zeros(m, dtype=bool)
    nCen    = np.zeros(m, dtype=int)

    # Compute probability for geometric component of density
    pgeom   = 1-(1-lmbda)*(prc+(1-prc)*pic)

    # Compute necessary bound for envelope condition
    bound = np.ones(m)
    if r < 1:
        bound *= (nobs + r - 1) / nobs

    # Run rejection sampling iterations
    nIter = 0
    while done.sum() < m:
        # Get indices of observations remaining to sample
        active  = np.where(~done)[0]

        # Propose from negative binomial distribution
        # This is almost correct, modulo the 0 vs. 1 minimum non-conjugacy
        prop    = np.random.negative_binomial(nobs[active] + r, pgeom[active],
                                              size=active.size)

        # Compute acceptance probability; bog standard
        u       = np.random.uniform(size=m-done.sum())
        pAccept = (nobs[active]+prop) / (nobs[active]+prop+r-1) * bound[active]

        # Alway accept for nObs == 0; in that case, our draw is exact
        pAccept[nobs[active]==0] = 1.0

        # Execute acceptance step and update done indicators
        nCen[active[u<pAccept]] = prop[u<pAccept]
        done[active] = u<pAccept

        nIter += 1

    # Add one to draws for nObs == 0; needed to meet constraint that all
    # peptides exist in at least one state.
    nCen = nCen + (nobs==0)
    return nCen

# Optimization and root-finding routines

def vectorizedBisection(f, lower, upper, f_args=tuple(), f_kwargs={},
                        tol=1e-10, maxIter=100, full_output=False):
    '''
    Find vector of roots of vectorized function using bisection.
    f should be a vectorized function that takes arguments x and f_args of
    compatible dimensions.
    In the iterations, f is called as:
        f(x, *f_args, **f_kwargs)
    '''
    # Initialization
    mid = lower/2. + upper/2.
    error = upper/2. - lower/2.

    f_lower = f(lower, *f_args, **f_kwargs)
    f_upper = f(upper, *f_args, **f_kwargs)

    # Check if the starting points are valid
    if np.any(np.sign(f_lower) * np.sign(f_upper) > 0):
        raise BisectionError(('Not all upper and lower bounds produce function'
                              ' values of different signs.'))

    # Iterate until convergence to tolerance
    t = 0
    while t <= maxIter and error.max() > tol:
        # Update function values
        f_mid = f(mid, *f_args, **f_kwargs)

        # Select direction to move
        below = np.sign(f_mid)*np.sign(f_lower) >= 0
        above = np.sign(f_mid)*np.sign(f_lower) <= 0

        # Update bounds and stored function values
        lower[below] = mid[below]
        f_lower[below] = f_mid[below]
        upper[above] = mid[above]
        f_upper[above] = f_mid[above]

        # Update midpoint and error
        mid = lower/2. + upper/2.
        error = upper/2. - lower/2.

        # Update iteration counter
        t += 1

    if full_output:
        return (mid, t)    
    
    return mid

def halley(f, fprime, f2prime, x0, f_args=tuple(), f_kwargs={},
           tol=1e-8, maxIter=200, full_output=False):
    '''
    Implements (vectorized) Halley's method for root finding.
    Requires function and its first two derivatives, all with first argument to
    search over (x) and the same later arguments (provided via f_args and
    f_kwargs). In the iterations, f, fprime, and f2prime are called as:
        f(x, *f_args, **f_kwargs)
    '''
    # Initialization
    t = 0
    x = copy.deepcopy(x0)

    while t < maxIter:
        # Evaluate the function and its derivatives
        fx          = f(x, *f_args, **f_kwargs)
        fprimex     = fprime(x, *f_args, **f_kwargs)
        f2primex    = f2prime(x, *f_args, **f_kwargs)

        # Update value of x
        x   = x - (2.*fx*fprimex) / (2.*fprimex**2 - fx*f2primex)

        # Update iteration counter
        t += 1

        # Convergence based upon absolute function value
        if(max(abs(fx)) < tol):
            break
    
    if full_output:
        return (x, t)
    
    return x

# Numerical integration functions

def laplaceApprox(f, xhat, info, f_args=tuple(), f_kwargs={}):
    '''
    Computes Laplace approximation to integral of f over real line.
    Takes mode xhat and observed information info as inputs.
    Fully comptatible with Numpy vector arguments so long as f is.
    '''
    integral = np.sqrt(2.*np.pi / info) * f(xhat, *f_args, **f_kwargs)
    return integral

# Specialized functions

def characterizeCensoredIntensityDist(eta_0, eta_1, mu, sigmasq,
                                      tol=1e-5, maxIter=200, bisectIter=10,
                                      bisectScale=6.):
    '''
    Constructs Gaussian approximation to conditional posterior of censored
    intensity likelihood. Approximates marginal p(censored | params) via Laplace
    approximation.

    Returns dictionary with three entries:
        1) yhat, the approximate mode of the given conditional distribution
        2) p_int_censored, the approximate probabilities of intensity-based
           censoring
        3) approx_sd, the approximate SDs of the conditional intensity
           distributions
    '''
    # Construct kwargs for calls to densities and their derivatives
    dargs = {'eta_0' : eta_0,
             'eta_1' : eta_1,
             'mu' : mu,
             'sigmasq' : sigmasq}
             
    # 1) Find mode of censored intensity density
    
    # First, start with a bit of bisection to get in basin of attraction for
    # Halley's method
    yhat = vectorizedBisection(f=deriv_logdcensored, f_kwargs=dargs,
                               lower=mu-bisectScale*np.sqrt(sigmasq),
                               upper=mu+bisectScale*np.sqrt(sigmasq),
                               tol=np.sqrt(tol), maxIter=bisectIter)
    
    # Second, run Halley's method to find the censored intensity distribution's
    # mode to much higher precision.
    yhat = halley(f=deriv_logdcensored, fprime=deriv2_logdcensored,
                  f2prime=deriv3_logdcensored, f_kwargs=dargs,
                  x0=yhat, tol=tol, maxIter=maxIter)
    
    # 2) Compute approximate SD of censored intensity distribution
    info        = -deriv2_logdcensored(yhat, **dargs)
    approx_sd   = np.sqrt(1./info)
    
    # 3) Use Laplace approximation to approximate p(int. censoring); this is the
    # normalizing constant of the given conditional distribution
    p_int_censored = laplaceApprox(f=dcensored, xhat=yhat, info=info,
                                   f_kwargs=dargs)
    
    # Return dictionary containing combined result
    result = {'yhat' : yhat,
              'p_int_censored' : p_int_censored,
              'approx_sd' : approx_sd}
    return result