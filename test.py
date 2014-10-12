###
# Copyright (c) 2013-2014, spline
# All rights reserved.
#
#
###

from supybot.test import *

class HardballTestCase(PluginTestCase):
    plugins = ('Hardball',)

    def testHardball(self):
        self.assertResponse('hardballchannel add #test NYY', "ERROR: '#test' is not a valid channel. You must add a channel that we are in.")
        self.assertResponse('hardballchannel del #test NYY', "ERROR: I do not have NYY in #test")


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
