""" Class definition for DumpRecorder, a recorder that prints
human-readable text output to a stream."""

import sys

from six import string_types, iteritems

from openmdao.core.mpi_wrap import MPI
from openmdao.recorders.base_recorder import BaseRecorder
from openmdao.util.record_util import format_iteration_coordinate
from openmdao.recorders.recorders import IterationRecorder

class DumpRecorder(BaseRecorder):
    """Dumps cases in a "pretty" form to `out`, which may be a string or a
    file-like object (defaults to ``stdout``). If `out` is ``stdout`` or
    ``stderr``, then that standard stream is used. Otherwise, if `out` is a
    string, then a file with that name will be opened in the current
    directory. If `out` is None, cases will be ignored. When called under
    MPI, the dumprecorder writes to a separate file for each rank, with the
    rank number appended to each filename. In this case, only variables that
    exist on all processes can be printed.
    """

    supported_recorders = [IterationRecorder]

    def __init__(self, out='stdout'):
        super(DumpRecorder, self).__init__()
        self._parallel = True

        if isinstance(out, string_types):

            if out == 'stdout':
                out = sys.stdout

            elif out == 'stderr':
                out = sys.stderr

            else:
                # Dump out to a separate file for each process if we are under
                # MPI
                if MPI: # pragma: no cover
                    if '.' in out:
                        parts = out.split('.')
                        parts[-2] += '_' + str(MPI.COMM_WORLD.rank)
                        out = '.'.join(parts)
                    else:
                        out += str(MPI.COMM_WORLD.rank)

                out = open(out, 'w')

        self.out = out

    def startup(self, group):
        """ Write out info that applies to the entire run.

        Args
        ----
        group : `Group`
            Group that owns this recorder.
        """
        super(DumpRecorder, self).startup(group)

    def record_iteration(self, params, unknowns, resids, metadata):
        """Dump the given run data in a "pretty" form.

        Args
        ----
        params : `VecWrapper`
            `VecWrapper` containing parameters. (p)

        unknowns : `VecWrapper`
            `VecWrapper` containing outputs and states. (u)

        resids : `VecWrapper`
            `VecWrapper` containing residuals. (r)

        metadata : dict
            Dictionary containing execution metadata (e.g. iteration coordinate).
        """

        if not self.out:  # if self.out is None, just do nothing
            return

        iteration_coordinate = metadata['coord']
        timestamp = metadata['timestamp']
        params, unknowns, resids = self._filter_vectors(params, unknowns, resids, iteration_coordinate)

        write = self.out.write
        fmat = "Timestamp: {0!r}\n"
        write(fmat.format(timestamp))

        fmat = "Iteration Coordinate: {0:s}\n"
        write(fmat.format(format_iteration_coordinate(iteration_coordinate)))

        if self.options['record_params']:
            write("Params:\n")
            for param, val in sorted(iteritems(params)):
                write("  {0}: {1}\n".format(param, str(val)))

        if self.options['record_unknowns']:
            write("Unknowns:\n")
            for unknown, val in sorted(iteritems(unknowns)):
                write("  {0}: {1}\n".format(unknown, str(val)))

        if self.options['record_resids']:
            write("Resids:\n")
            for resid, val in sorted(iteritems(resids)):
                write("  {0}: {1}\n".format(resid, str(val)))

        # Flush once per iteration to allow external scripts to process the data.
        self.out.flush()
