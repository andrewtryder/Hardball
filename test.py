###
# Copyright (c) 2013-2014, spline
# All rights reserved.
#
#
###

from supybot.test import *

class HardballTestCase(ChannelPluginTestCase):
    plugins = ('Hardball',)

    def testHardball(self):
        self.assertResponse('hardballchannel add #test NYY', "I have added NYY into #test")
        self.assertResponse('hardballchannel del #test NYY', "I have successfully removed NYY from #test")


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
