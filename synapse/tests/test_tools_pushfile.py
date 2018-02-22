from synapse.tests.common import *

import synapse.axon as s_axon
import synapse.cortex as s_cortex
import synapse.daemon as s_daemon
import synapse.neuron as s_neuron

import synapse.tools.pushfile as s_pushfile

class TestPushFile(SynTest):

    def test_tools_pushfile(self):
        self.skipLongTest()
        with self.getAxonCore() as env:

            visipath = os.path.join(env.path, 'visi.txt')
            with open(visipath, 'wb') as fd:
                fd.write(b'visi')

            with s_daemon.Daemon() as dmon:

                dmonlink = dmon.listen('tcp://127.0.0.1:0/')
                dmonport = dmonlink[1].get('port')
                dmon.share('core', env.core)

                coreurl = 'tcp://127.0.0.1:%d/core' % dmonport
                axonhost, axonport = env.axon.getCellAddr()
                axonauth = env.axon.getCellAuth()

                axonconf = {'auth': axonauth, 'host': axonhost, 'port': axonport} # ???
                env.core.setConfOpt('axon:conf', axonconf)  # FIXME need some event to know when its ready

                outp = self.getTestOutp()
                s_pushfile.main(['--tags', 'foo.bar,baz.faz', coreurl, visipath], outp=outp)

                node = env.core.getTufoByProp('file:bytes')
                self.eq(node[1].get('file:bytes'), '442f602ecf8230b2a59a44b4f845be27')
                self.eq(node[1].get('file:bytes:size'), 4)
                self.nn(node[1].get('#foo'))
                self.nn(node[1].get('#foo.bar'))
                self.nn(node[1].get('#baz'))
                self.nn(node[1].get('#baz.faz'))

                # FIXME this test doesn't make sure that the file is in the axon...
