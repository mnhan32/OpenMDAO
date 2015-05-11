
from collections import namedtuple, OrderedDict
import numpy
from openmdao.core.basicimpl import BasicImpl

ViewTuple = namedtuple('ViewTuple', 'unknowns, dunknowns, resids, dresids, params, dparams')

class VarManagerBase(object):
    """Base class for a manager of the data transfer of a possibly distributed
    collection of variables.

    Parameters
    ----------
        connections : dict
            a dictionary mapping the pathname of a target variable to the
            pathname of the source variable that it is connected to
    """
    def __init__(self, connections):
        self.connections = connections
        self.params    = None
        self.dparams   = None
        self.unknowns  = None
        self.dunknowns = None
        self.resids    = None
        self.dresids   = None
        self.data_xfer = {}

    def __getitem__(self, name):
        """Retrieve unflattened value of named variable

        Parameters
        ----------
        name : str   OR   tuple : (name, vector)
             the name of the variable to retrieve from the unknowns vector OR
             a tuple of the name of the variable and the vector to get it's
             value from.

        Returns
        -------
        the unflattened value of the given variable
        """
        if isinstance(name, tuple):
            name, vector = name
        else:
            vector = 'unknowns'
        try:
            return getattr(self, vector)[name]
        except KeyError:
            raise KeyError('%s is not in the %s vector for this system' %
                           (name, vector))

    def _setup_data_transfer(self, sys_pathname, my_params):
        """Create `DataXfer` objects to handle data transfer for all of the
           connections that involve paramaters for which this `VarManager`
           is responsible.

           Parameters
           ----------
           sys_pathname : str
               Absolute pathname of the `System` that will own this `VarManager`.

           my_params : list
               list of pathnames for parameters that the VarManager is
               responsible for propagating
        """

        # collect all flattenable var sizes from self.unknowns
        usize = [m['size'] for m in self.unknowns.values()
                     if not m.get('noflat')]

        # create a 1x<num_flat_vars> numpy array with the sizes of each var
        self._local_sizes = numpy.array([[usize]])

        # we would do an Allgather of the local_sizes in the distributed case so all
        # processes would know the sizes of all variables (needed to determine distributed
        # indices)

        # create a 1x1 numpy array to hold the param values when they're
        # transferred from their sources.
        owned = OrderedDict()
        psize = sum([m['size'] for n,m in self.params.items()
                             if m.get('owned') and not m.get('noflat')])
        self._param_sizes = numpy.array([[psize]])

        #TODO: invesigate providing enough system info here to determine what types of scatters
        # are necessary (for example, full scatter isn't needed except when solving using jacobi,
        # so why allocate space for the index arrays?)

        xfer_dict = {}
        for param, unknown in self.connections.items():
            if param in my_params:
                # remove our system pathname from the abs pathname of the param and
                # get the subsystem name from that
                if sys_pathname:
                    start = len(sys_pathname)+1
                else:
                    start = 0
                tgt_sys = param[start:].split(':', 1)[0]
                src_idx_list, dest_idx_list, flat_conns, noflat_conns = \
                                   xfer_dict.setdefault(tgt_sys, ([],[],[],[]))
                urelname = self.unknowns.get_relative_varname(unknown)
                prelname = self.params.get_relative_varname(param)
                noflat = self.unknowns.metadata(urelname)[0].get('noflat')
                if noflat:
                    noflat_conns.append((prelname, urelname))
                else:
                    flat_conns.append((prelname, urelname))
                    src_idx_list.append(self.unknowns.get_idxs(urelname))
                    dest_idx_list.append(self.params.get_idxs(prelname))

        for tgt_sys, (srcs, tgts, flat_conns, noflat_conns) in xfer_dict.items():
            src_idxs, tgt_idxs = self.unknowns.merge_idxs(srcs, tgts)
            self.data_xfer[tgt_sys] = self.implFactory.createDataXfer(src_idxs, tgt_idxs, flat_conns, noflat_conns)

        # create a jacobi DataXfer object that combines all of the
        # individual subsystem src_idxs, tgt_idxs, and noflat_conns
        # TODO: this is only needed for jacobi, so only create if we're using jacobi
        full_srcs = []
        full_tgts = []
        full_flats = []
        full_noflats = []
        for src, tgts, flats, noflats in xfer_dict.values():
            full_srcs.extend(src)
            full_tgts.extend(tgts)
            full_flats.extend(flats)
            full_noflats.extend(noflats)
        src_idxs, tgt_idxs = self.unknowns.merge_idxs(full_srcs, full_tgts)
        self.data_xfer[''] = self.implFactory.createDataXfer(src_idxs, tgt_idxs, full_flats, full_noflats)

    def _transfer_data(self, target_system='', mode='fwd', deriv=False):
        """Transfer data to/from target_system depending on mode.

        Parameters
        ----------
        target_system : str
            Name of the target `System`.  A name of '' indicates that data
            should be transfered to all subsystems at once.

        mode : { 'fwd', 'rev' }, optional
            Specifies forward or reverse data transfer.

        deriv : bool, optional
            If True, perform a data transfer between derivative `VecWrapper`s
        """
        x = self.data_xfer.get(target_system)
        if x is not None:
            if deriv:
                x.transfer(self.dunknowns, self.dparams, mode)
            else:
                x.transfer(self.unknowns, self.params, mode)


class VarManager(VarManagerBase):
    """A manager of the data transfer of a possibly distributed
    collection of variables.

    Parameters
    ----------
    params_dict : dict
        dictionary of metadata for all parameters

    unknowns_dict : dict
        dictionary of metadata for all unknowns

    my_params : list
        list of pathnames for parameters that this `VarManager` is
        responsible for propagating

    connections : dict
        a dictionary mapping the pathname of a target variable to the
        pathname of the source variable that it is connected to
    """
    def __init__(self, sys_pathname, params_dict, unknowns_dict, my_params, connections, impl=None):
        super(VarManager, self).__init__(connections)

        if impl is None:
            self.implFactory = BasicImpl
        else:
            raise RuntimeError('%s implementation of VecWrapper is not avaiable.')

        # create implementation specific VecWrappers
        self.unknowns  = self.implFactory.createVecWrapper()
        self.dunknowns = self.implFactory.createVecWrapper()
        self.resids    = self.implFactory.createVecWrapper()
        self.dresids   = self.implFactory.createVecWrapper()
        self.params    = self.implFactory.createVecWrapper()
        self.dparams   = self.implFactory.createVecWrapper()

        # populate the VecWrappers with data
        self.unknowns.setup_source_vector(unknowns_dict, store_noflats=True)
        self.dunknowns.setup_source_vector(unknowns_dict)
        self.resids.setup_source_vector(unknowns_dict)
        self.dresids.setup_source_vector(unknowns_dict)

        self.params.setup_target_vector(None, params_dict, self.unknowns,
                                              my_params, connections, store_noflats=True)
        self.dparams.setup_target_vector(None, params_dict, self.unknowns,
                                               my_params, connections)

        self._setup_data_transfer(sys_pathname, my_params)


class ViewVarManager(VarManagerBase):
    """A manager of the data transfer of a possibly distributed collection of
    variables.  The variables are based on views into an existing VarManager.

    Parameters
    ----------
    parent_vm : `VarManager`
        the `VarManager` which provides the `VecWrapper`s on which to create views

    params_dict : dict
        dictionary of metadata for all parameters

    unknowns_dict : dict
        dictionary of metadata for all unknowns

    my_params : list
        list of pathnames for parameters that this `VarManager` is
        responsible for propagating

    connections : dict
        a dictionary mapping the pathname of a target variable to the
        pathname of the source variable that it is connected to
    """
    def __init__(self, parent_vm, sys_pathname, params_dict, unknowns_dict, my_params, connections):
        super(ViewVarManager, self).__init__(connections)

        self.implFactory = parent_vm.implFactory

        self.unknowns, self.dunknowns, self.resids, self.dresids, self.params, self.dparams = \
            create_views(parent_vm, sys_pathname, params_dict, unknowns_dict, my_params, connections)

        self._setup_data_transfer(sys_pathname, my_params)



def create_views(parent_vm, sys_pathname, params_dict, unknowns_dict, my_params, connections):
    """A manager of the data transfer of a possibly distributed collection of
    variables.  The variables are based on views into an existing VarManager.

    Parameters
    ----------
    parent_vm : `VarManager`
        the `VarManager` which provides the `VecWrapper`s on which to create views

    sys_pathname : str
        pathname of the system for which the views are being created

    params_dict : dict
        dictionary of metadata for all parameters

    unknowns_dict : dict
        dictionary of metadata for all unknowns

    my_params : list
        list of pathnames for parameters that this `VarManager` is
        responsible for propagating

    connections : dict
        a dictionary mapping the pathname of a target variable to the
        pathname of the source variable that it is connected to

    Returns
    -------
    `ViewTuple`
        a namedtuple of six (6) `VecWrapper`s:
        unknowns, dunknowns, resids, dresids, params, dparams
    """

    # map relative name in parent to corresponding relative name in this view
    umap = get_relname_map(parent_vm.unknowns, unknowns_dict, sys_pathname)

    unknowns  = parent_vm.unknowns.get_view(umap)
    dunknowns = parent_vm.dunknowns.get_view(umap)
    resids    = parent_vm.resids.get_view(umap)
    dresids   = parent_vm.dresids.get_view(umap)

    params  = parent_vm.implFactory.createVecWrapper()
    dparams = parent_vm.implFactory.createVecWrapper()

    params.setup_target_vector(parent_vm.params, params_dict, unknowns,
                               my_params, connections, store_noflats=True)
    dparams.setup_target_vector(parent_vm.dparams, params_dict, unknowns,
                                my_params, connections)

    return ViewTuple(unknowns, dunknowns, resids, dresids, params, dparams)


def get_relname_map(unknowns, unknowns_dict, child_name):
    """
    Parameters
    ----------
    unknowns : `VecWrapper`
        A dict-like object containing variables keyed using relative names.

    unknowns_dict : `OrderedDict`
        An ordered mapping of absolute variable name to its metadata.

    child_name : str
        The pathname of the child for which to get relative name

    Returns
    -------
    dict
        Maps relative name in parent (owner of unknowns and unknowns_dict) to
        the corresponding relative name in the child, where relative name may
        include the 'promoted' name of a variable.
    """
    # unknowns is keyed on name relative to the parent system/varmanager
    # unknowns_dict is keyed on absolute pathname
    umap = {}
    for rel, meta in unknowns.items():
        abspath = meta['pathname']
        if abspath.startswith(child_name+':'):
            newmeta = unknowns_dict.get(abspath)
            if newmeta is not None:
                newrel = newmeta['relative_name']
            else:
                newrel = rel
            umap[rel] = newrel

    return umap
