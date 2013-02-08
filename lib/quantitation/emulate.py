import numpy as np
from scipy import linalg
from scipy import spatial

# Covariance functions

def approx_quantile(coverage_prob, d, n):
  '''
  Compute approximate coverage_prob quantile of maximal distance between n
  spherically-distributed points with identity covariance and the origin.

  Arguments
  ---------
  coverage_prob : number
    Probability that maximum distance is less than returned value.
  d : integer
    Number of dimensions.
  n : integer
    Number of distances over which points are distributed.

  Returns
  -------
  q : float
    Approximate coverage_prob quantile of maximum distance distribution.
  '''
  return np.sqrt(np.sqrt(2. * d) * (-np.log(-np.log(coverage_prob)) /
                                    np.sqrt(2*np.log(n)) + 2*np.log(n) -
                                    0.5*np.log(np.log(n))))

def cov_sqexp(r, scale=1.):
  '''
  Squared exponential (Gaussian) covariance function with given scale.
  '''
  return np.exp( - (r / scale)**2 )

def build_grid(d, grid_radius=1., grid_transform=None, grid_min_spacing=0.5,
         grid_shape='spherical'):
  '''
  Build regular cubic or spherical grid in d dimensions.

  Arguments
  ---------
  d : integer
    Number of dimensions for grid.
  grid_radius : number
    Minimum radius of grid before transform, inclusive.
  grid_transform : np.ndarray or matrix
    Optional d x d nd.array or matrix providing transformation from cubic or
    spherical grid into space of interest. Should be lower-triangular and
    positive-definite.
  grid_min_spacing : float
    Minimum spacing of grid after transformation.
  grid_shape : string
    Shape of grid, 'cubic' or 'spherical'. Spherical is truncated cubic grid.

  Returns
  -------
  grid : ndarray
    d x n_grid ndarray containing the computed grid. Each column is a single
    vector in R^d.
  '''
  # Find eigenvalues of transformation
  if grid_transform is None:
    transform_eigenvalues = np.ones(d)
  else:
    transform_eigenvalues = np.diag(grid_transform)

  # Build grid before rotation and scaling, adjusting spacing as needed
  grid_radius = float(grid_radius)
  h_grid = [grid_radius / np.ceil(grid_radius / grid_min_spacing * v) for v in
            transform_eigenvalues]
  dim_grid = [int(2 * grid_radius / h + 1) for h in h_grid]
  dim_grid_float = np.array(dim_grid, dtype=float)
  grid = np.mgrid[tuple(slice(0, l) for l in dim_grid)]
  grid = np.array([z.flatten() for z in grid], dtype=float).T
  grid /= (dim_grid_float - 1.) / 2.
  grid -= 1.

  # Truncate to sphere if requested
  if grid_shape[:5] == 'spher':
    grid = grid[np.sum(grid**2, 1) <= 1]

  # Rescale for radius
  grid *= grid_radius

  # Transform and recenter
  if grid_transform is not None:
    grid = np.dot(grid, grid_transform.T)

  return grid

def build_emulator(f, center, slope_mean=None, cov=cov_sqexp, grid_radius=1.,
                   grid_transform=None, grid_min_spacing=0.5,
                   grid_shape='spherical', f_args=(), f_kwargs={}, cov_args=(),
                   cov_kwargs={}, min_cov=1e-9, cov_step=10.,
                   max_log10_condition=10.):
  '''
  Build Gaussian processor emulator for given function.
  
  Parameters
  ---------- 
  f : function
    Function to emulate. Must take a d x n matrix as its first argument and
    return return a k x n ndarray containing the function value for each
    d-dimensional column of the input matrix. Called as
    f(x, *f_args, **f_kwargs) in evaluations.
  center : d length ndarray
    Center of sampling region for emulator.
  slope_mean : d x d ndarray
    Optional linear approximation for f(x - center). Must be lower-triangular.
  cov : function
    Covariance function for Gaussian process. Must accept ndarray of distances
    as first argument and return an ndarray of the same dimension.  Called as
    cov(dm, *cov_args, **cov_kwargs).
  grid_radius : number
    Minimum radius of grid before transform, inclusive.
  grid_transform : np.ndarray or matrix
    Optional d x d nd.array or matrix providing transformation from cubic or
    spherical grid into space of interest. Should be lower-triangular and
    positive-definite.
  grid_min_spacing : float
    Minimum spacing of grid after transformation.
  grid_shape : string
    Shape of grid, 'cubic' or 'spherical'. Spherical is truncated cubic grid.
  f_args : tuple
    Tuple of additional positional arguments for f.
  f_kwargs : dict
    Dictionary of additional kw arguments for f.
  cov_args : tuple
    Tuple of additional positional arguments for cov.
  cov_kwargs : tuple
    Dictionary of additional kw arguments for cov.
  min_cov : float
    Initial minimum covariance; covariance matrix is truncated at this value.
  cov_step : float
    Multiplicative step for minimum covariance (upward) if covariance matrix is
    computationally singular.
  max_log10_condition : number
    Maximum log10 condition number to accept for covariance matrix.  Truncation
    continues at min_cov * cov_step**k until this is satisfied.

  Returns
  -------
  A dictionary containing:
    - grid : d x n_grid ndarray
      The computed grid for approximation.
    - v : n_grid x k ndarray
      Array for approximation.
    - center : d length ndarray
      Center of emulation region.
    - slope_mean : d x d ndarray
      Optional slope of linear mean function. Can be None.
  '''
  # Get dimensions
  d = np.size(center)

  # Build grid
  grid = build_grid(d=d, grid_radius=grid_radius, grid_transform=grid_transform,
                    grid_min_spacing=grid_min_spacing, grid_shape=grid_shape)
  grid += center

  # Evaluate function over grid
  f_values = f(grid.T, *f_args, **f_kwargs)
  
  if slope_mean is not None:
    f_values -= np.dot(slope_mean, (grid - center).T)

  # Compute covariance matrix for GP
  C = spatial.distance_matrix(grid, grid, p=2)
  C = cov(C, *cov_args, **cov_kwargs)
  
  # Truncate at minimum covariance
  C[C < min_cov] = 0.
  
  # Continue to truncate at higher covariances if needed for numerical
  # stability
  svals = linalg.svdvals(C)
  log10_condition = np.ptp(np.log10(svals))
  while log10_condition > max_log10_condition:
    min_cov *= cov_step
    C[C < min_cov] = 0.
    svals = linalg.svdvals(C)
    log10_condition = np.ptp(np.log10(svals))

  # Compute vector for subsequent approximations
  v = linalg.solve(C, f_values.T)

  # Build output
  emulator = {'grid' : grid, 'v' : v,
              'center' : center, 'slope_mean' : slope_mean}

  return emulator

def evaluate_emulator(x, emulator, cov, cov_args=(), cov_kwargs={}):
  '''
  Evaluates emulator at given point or sequence of points
  
  Arguments
  ---------
  x : ndarray
    Array of length d or of dimension d x m, with each column containing a point
    at which to evaluate the emulator.
  emulator : dict
    Dictionary as output by build_emulator containing grid and v.
  cov : function
    Covariance function for Gaussian process. Must accept ndarray of distances
    as first argument and return an ndarray of the same dimension.  Called as
    cov(dm, *cov_args, **cov_kwargs).
  cov_args : tuple
    Tuple of additional positional arguments for cov.
  cov_kwargs : tuple
    Dictionary of additional kw arguments for cov.
  
  Returns
  -------
  f_hat : ndarray
    Array of size k x m containing estimated values of function.
  '''
  # Convert x to matrix if needed
  if not type(x) is np.ndarray:
    x = np.array(x)
  if len(x.shape) < 2:
    x = x[:, np.newaxis]

  # Evaluate distances between x and grid
  C = spatial.distance_matrix(x.T, emulator['grid'])
  C = cov(C, *cov_args, **cov_kwargs)

  # Estimate function values at x
  f_hat = np.dot(emulator['v'].T, C.T)
  
  # Add linear term if needed
  if emulator['slope_mean'] is not None:
    f_hat += np.dot(emulator['slope_mean'], (x.T - emulator['center']).T)

  if x.shape[1] < 2:
    f_hat = f_hat[:,0]

  return f_hat

def evaluate_emulator_nogrid(x, v, center, cov, slope_mean=None, grid_radius=1.,
                             grid_transform=None, grid_min_spacing=0.5,
                             grid_shape='spherical', cov_args=(),
                             cov_kwargs={}):
  '''
  Evaluates emulator at given point or sequence of points, reconstructing the
  grid from other arguments. This is useful in communication-limited settings
  where the grid parameters are common knowledge.
  
  Arguments
  ---------
  x : ndarray
    Array of length d or of dimension d x m, with each column containing a point
    at which to evaluate the emulator.
  - v : n_grid x k ndarray
    Matrix for approximation.
  center : d length ndarray
    Center of emulation region.
  cov : function
    Covariance function for Gaussian process. Must accept ndarray of distances
    as first argument and return an ndarray of the same dimension.  Called as
    cov(dm, *cov_args, **cov_kwargs).
  slope_mean : d x d ndarray
    Optional slope of linear mean function. Can be None.
  grid_radius : number
    Minimum radius of grid before transform, inclusive.
  grid_transform : np.ndarray or matrix
    Optional d x d nd.array or matrix providing transformation from cubic or
    spherical grid into space of interest. Should be lower-triangular and
    positive-definite.
  grid_min_spacing : float
    Minimum spacing of grid after transformation.
  grid_shape : string
    Shape of grid, 'cubic' or 'spherical'. Spherical is truncated cubic
    grid.
  cov_args : tuple
    Tuple of additional positional arguments for cov.
  cov_kwargs : tuple
    Dictionary of additional kw arguments for cov.
  
  Returns
  -------
  f_hat : ndarray
    Array of length m containing estimated values of function.
  '''
  # Convert x to matrix if needed
  if not type(x) is np.ndarray:
    x = np.array(x)
  if len(x.shape) < 2:
    x = x[:, np.newaxis]

  # Build grid
  grid = build_grid(d=d, grid_radius=grid_radius, grid_transform=grid_transform,
                    grid_min_spacing=grid_min_spacing, grid_shape=grid_shape)
  grid += center

  # Evaluate distances between x and grid
  C = spatial.distance_matrix(x.T, grid)
  C = cov(C, *cov_args, **cov_kwargs)

  # Estimate function values at x
  f_hat = np.dot(C, v).T
  
  # Add linear term if needed
  if slope_mean is not None:
    f_hat += np.dot(slope_mean, (x.T - slope_mean).T)
  
  if x.shape[1] < 2:
    f_hat = f_hat[:,0]

  return f_hat

def aggregate_emulators(emulators):
  '''
  Aggregate list or tuple of emulators into a single emulator for their sum.

  Arguments
  ---------
  emulators : list-like
    List-like collection of emulators

  Returns
  -------
  emulator : dict
    A dictionary for the combined emulator containing
    - grid : d x n_grid ndarray
      The computed grid for approximation.
    - v : n_grid x k ndarray
      Array for approximation.
    - center : d length ndarray
      Center of emulation region.
    - slope_mean : d x d ndarray
      Optional slope of linear mean function. Can be None.
  '''
  # Get dimensions
  d = np.size(emulators[0]['center'])
  k = np.shape(emulators[0]['v'])[1]
  n_grids = np.array([emulator['grid'].shape[0] for emulator in emulators],
                     dtype=int)
  n_grid_agg = np.sum(n_grids)
  
  # Allocate arrays for combined emulator
  v_agg = np.empty((n_grid_agg, k))
  grid_agg = np.empty((n_grid_agg, d))
  center_agg = np.zeros(d)
  slope_mean_agg = np.zeros((d, d))

  # Iterate over emulators
  start = 0
  for i, emulator in enumerate(emulators):
    v_agg[start:start + n_grids[i], :] = emulator['v']
    grid_agg[start:start + n_grids[i], :] = emulator['grid']
    start += n_grids[i]

    if emulator['slope_mean'] is not None:
      slope_mean_agg += emulator['slope_mean']
      center_agg += np.dot(emulator['slope_mean'], emulator['center'])
  
  if np.max(np.abs(slope_mean_agg)) > 0:
    center_agg = linalg.solve_triangular(slope_mean_agg, center_agg, lower=True)

  emulator = {'grid' : grid_agg, 'v' : v_agg,
              'center' : center_agg, 'slope_mean' : slope_mean_agg}

  return emulator

