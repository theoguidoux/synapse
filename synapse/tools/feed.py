import os
import sys
import time
import shutil
import asyncio
import logging
import argparse
import tempfile
import contextlib
import synapse.common as s_common
import synapse.cortex as s_cortex
import synapse.telepath as s_telepath

import synapse.lib.cmdr as s_cmdr
import synapse.lib.const as s_const
import synapse.lib.output as s_output
import synapse.lib.msgpack as s_msgpack

logger = logging.getLogger(__name__)

@contextlib.contextmanager
def getTempDir():
    tempdir = tempfile.mkdtemp()

    try:
        yield tempdir

    finally:
        shutil.rmtree(tempdir, ignore_errors=True)

def getItems(*paths):
    items = []
    for path in paths:
        if path.endswith('.json'):
            item = s_common.jsload(path)
            if not isinstance(item, list):
                item = [item]
            items.append((path, item))
        elif path.endswith(('.yaml', '.yml')):
            item = s_common.yamlload(path)
            if not isinstance(item, list):
                item = [item]
            items.append((path, item))
        elif path.endswith('.mpk'):
            genr = s_msgpack.iterfile(path)
            items.append((path, genr))
        else:  # pragma: no cover
            logger.warning('Unsupported file path: [%s]', path)
    return items

async def addFeedData(core, outp, feedformat, debug=False, *paths, chunksize=1000, offset=0):

    items = getItems(*paths)
    for path, item in items:
        bname = os.path.basename(path)
        tick = time.time()
        outp.printf(f'Adding items from [{path}]')
        foff = 0
        for chunk in s_common.chunks(item, chunksize):

            clen = len(chunk)
            if offset and foff + clen < offset:
                # We have not yet encountered a chunk which
                # will include the offset size.
                foff += clen
                continue

            await core.addFeedData(feedformat, chunk)

            foff += clen
            outp.printf(f'Added [{clen}] items from [{bname}] - offset [{foff}]')

        tock = time.time()
        outp.printf(f'Done consuming from [{bname}]')
        outp.printf(f'Took [{tock - tick}] seconds.')
    if debug:
        cmdr = await s_cmdr.getItemCmdr(core, outp)
        await cmdr.runCmdLoop()

async def main(argv, outp=None):

    if outp is None:  # pragma: no cover
        outp = s_output.OutPut()

    pars = makeargparser()
    opts = pars.parse_args(argv)

    if opts.offset:
        if len(opts.files) > 1:
            outp.printf('Cannot start from a arbitrary offset for more than 1 file.')
            return 1

        outp.printf(f'Starting from offset [{opts.offset}] - it may take a while'
                    f' to get to that location in the input file.')

    if opts.test:
        with getTempDir() as dirn:
            s_common.yamlsave({'layer:lmdb:mapsize': s_const.gibibyte * 5},
                              dirn, 'cell.yaml')
            async with await s_cortex.Cortex.anit(dirn) as core:
                for mod in opts.modules:
                    outp.printf(f'Loading [{mod}]')
                    await core.loadCoreModule(mod)
                await addFeedData(core, outp, opts.format, opts.debug,
                                  chunksize=opts.chunksize,
                                  offset=opts.offset,
                                  *opts.files)

    elif opts.cortex:
        async with await s_telepath.openurl(opts.cortex) as core:
            await addFeedData(core, outp, opts.format, opts.debug,
                              chunksize=opts.chunksize,
                              offset=opts.offset,
                              *opts.files)

    else:  # pragma: no cover
        outp.printf('No valid options provided [%s]', opts)
        return 1

    return 0

def makeargparser():
    desc = 'Command line tool for ingesting data into a cortex'
    pars = argparse.ArgumentParser('synapse.tools.ingest', description=desc)

    muxp = pars.add_mutually_exclusive_group(required=True)
    muxp.add_argument('--cortex', '-c', type=str,
                      help='Cortex to connect and add nodes too.')
    muxp.add_argument('--test', '-t', default=False, action='store_true',
                      help='Perform a local ingest against a temporary cortex.')

    pars.add_argument('--debug', '-d', default=False, action='store_true',
                      help='Drop to interactive prompt to inspect cortex after loading data.')
    pars.add_argument('--format', '-f', type=str, action='store', default='syn.ingest',
                      help='Feed format to use for the ingested data.')
    pars.add_argument('--modules', '-m', type=str, action='append', default=[],
                      help='Additional modules to load locally with a test Cortex.')
    pars.add_argument('--chunksize', type=int, action='store', default=1000,
                      help='Default chunksize for iterating over items.')
    pars.add_argument('--offset', type=int, action='store', default=0,
                      help='Item offset to start consuming msgpack files from.')
    pars.add_argument('files', nargs='*', help='json/yaml/msgpack feed files')

    return pars

async def _main():  # pragma: no cover
    s_common.setlogging(logger, 'DEBUG')
    return await main(sys.argv[1:])

if __name__ == '__main__':  # pragma: no cover
    sys.exit(asyncio.run(_main()))
