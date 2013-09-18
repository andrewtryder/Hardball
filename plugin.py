# -*- coding: utf-8 -*-
###
# Copyright (c) 2013, spline
# All rights reserved.
#
#
###
# my libs
from __future__ import division  # so we're not Bankers rounding.
from base64 import b64decode
import cPickle as pickle
import datetime
import re
from BeautifulSoup import BeautifulSoup
import sqlite3
import os.path
# extra supybot libs
import supybot.conf as conf
import supybot.schedule as schedule
import supybot.ircmsgs as ircmsgs
# supybot libs
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Hardball')
except:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x:x

class Hardball(callbacks.Plugin):
    """Add the help for "@plugin help Hardball" here
    This should describe *how* to use this plugin."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(Hardball, self)
        self.__parent.__init__(irc)
        # initial states for channels.
        self.channels = {}  # dict for channels with values as teams/ids
        self._loadpickle()  # load saved data.
        # initial states for games.
        self.games = None
        self.nextcheck = None
        # fetchhost system.
        self.fetchhost = None
        self.fetchhostcheck = None
        # fill in the blanks.
        if not self.games:
            self.games = self._fetchgames()
        # now schedule our events.
        def checkhardballcron():
            self.checkhardball(irc)
        try:  # check scores.
            schedule.addPeriodicEvent(checkhardballcron, self.registryValue('checkInterval'), now=False, name='checkhardball')
        except AssertionError:
            try:
                schedule.removeEvent('checkhardball')
            except KeyError:
                pass
            schedule.addPeriodicEvent(checkhardballcron, self.registryValue('checkInterval'), now=False, name='checkhardball')

    def die(self):
        try:
            schedule.removeEvent('checkhardball')
        except KeyError:
            pass
        self.__parent.die()

    ######################
    # INTERNAL FUNCTIONS #
    ######################

    def _httpget(self, url, h=None, d=None, l=True):
        """General HTTP resource fetcher. Pass headers via h, data via d, and to log via l."""

        if self.registryValue('logURLs') and l:
            self.log.info(url)

        try:
            h = {"User-Agent":"Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:17.0) Gecko/20100101 Firefox/17.0"}
            page = utils.web.getUrl(url, headers=h)
            return page
        except utils.web.Error as e:
            self.log.error("ERROR opening {0} message: {1}".format(url, e))
            return None

    def _utcnow(self):
        """Calculate Unix timestamp from GMT."""

        ttuple = datetime.datetime.utcnow().utctimetuple()
        _EPOCH_ORD = datetime.date(1970, 1, 1).toordinal()
        year, month, day, hour, minute, second = ttuple[:6]
        days = datetime.date(year, month, 1).toordinal() - _EPOCH_ORD + day - 1
        hours = days*24 + hour
        minutes = hours*60 + minute
        seconds = minutes*60 + second
        return seconds

    ###########################################
    # INTERNAL CHANNEL POSTING AND DELEGATION #
    ###########################################

    def _post(self, irc, awayid, homeid, message):
        """Posts message to a specific channel."""

        # how this works is we have an incoming away and homeid. we then look up these (along with 0)
        # against the self.channels dict (k=channel, v=set of #). then, if any of the #'s match in the v
        # we insert this back into postchans so that the function posts the message into the proper channel(s).
        if len(self.channels) == 0:  # first, we have to check if anything is in there.
            #self.log.error("ERROR: I do not have any channels to output in.")
            return
        # we do have channels. lets go and check where to put what.
        teamids = [awayid, homeid, '0'] # append 0 so we output. needs to be strings.
        postchans = [k for (k, v) in self.channels.items() if __builtins__['any'](z in v for z in teamids)]
        # iterate over each.
        for postchan in postchans:
            try:
                # check to see if we should prefix output.
                if self.registryValue('prefix', postchan):  # we do so lets prefix and output.
                    message = "{0}{1}".format(self.registryValue('prefixString', postchan), message)
                # now send  the actual output.
                irc.queueMsg(ircmsgs.privmsg(postchan, message))
            except Exception as e:
                self.log.error("ERROR: Could not send {0} to {1}. {2}".format(message, postchan, e))

    ##############################
    # INTERNAL CHANNEL FUNCTIONS #
    ##############################

    def _loadpickle(self):
        """Load channel data from pickle."""

        try:
            datafile = open(conf.supybot.directories.data.dirize(self.name()+".pickle"), 'rb')
            try:
                dataset = pickle.load(datafile)
            finally:
                datafile.close()
        except IOError:
            return False
        # restore.
        self.channels = dataset["channels"]
        return True

    def _savepickle(self):
        """Save channel data to pickle."""

        data = {"channels": self.channels}
        try:
            datafile = open(conf.supybot.directories.data.dirize(self.name()+".pickle"), 'wb')
            try:
                pickle.dump(data, datafile)
            finally:
                datafile.close()
        except IOError:
            return False
        return True

    #########################
    # TEAM DB AND FUNCTIONS #
    #########################

    def _teams(self, team=None):
        """Main team database. Translates ID into team and can also return table."""

        table = {'0':'ALL', '1':'BAL', '2':'BOS', '3':'LAA', '4':'CHW', '5':'CLE', '6':'DET',
                 '7':'KC', '8':'MIL', '9':'MIN', '10':'NYY', '11':'OAK', '12':'SEA',
                 '13':'TEX', '14':'TOR', '15':'ATL', '16':'CHC', '17':'CIN', '18':'HOU',
                 '19':'LAD', '20':'WSH', '21':'NYM', '22':'PHI', '23':'PIT', '24':'STL',
                 '25':'SD', '26':'SF', '27':'COL', '28':'MIA', '29':'ARI', '30':'TB',
                 '31':'AL', '32':'NL'}
        # return
        if team:  # if we got a team.
            if team in table:  # if we get a valid #, return the team.
                return table[team]
            else:  # invalid, so we return the number back.
                return team
        else:  # no team so return the table
            return table

    def _teamnametoid(self, teamid):
        """Translates a team name like NYY into its ID: (NYY->10)."""

        # reverse k/v of self._teams() table.
        teams = dict(zip(*zip(*self._teams().items())[::-1]))
        # return from table.
        return teams[str(teamid)]

    def _validteam(self, team=None):
        """Identical to _teams but reverses k/v for input/output."""

        # reverse k/v of self._teams()
        validteams = dict(zip(*zip(*self._teams().items())[::-1]))
        # check.
        if team:  # if we have a team.
            if team in validteams:
                return True
            else:  # no team matches
                return None
        else:  # return the dict.
            return validteams

    ####################
    # FETCH OPERATIONS #
    ####################

    def _fetchhost(self):
        """Return the host for fetch operations."""

        utcnow = self._utcnow()
        # if we don't have the host, lastchecktime, or fetchhostcheck has passed, we regrab.
        if ((not self.fetchhostcheck) or (not self.fetchhost) or (self.fetchhostcheck < utcnow)):
            url = b64decode('aHR0cDovL2F1ZC5zcG9ydHMueWFob28uY29tL2Jpbi9ob3N0bmFtZQ==')
            html = self._httpget(url)  # try and grab.
            if not html:
                self.log.error("ERROR: _fetchhost: could not fetch {0}")
                return None
            # now that we have html, make sure its valid.
            if html.startswith("aud"):
                fhurl = 'http://%s' % (html.strip())
                self.fetchhost = fhurl  # set the url.
                self.fetchhostcheck = utcnow+3600  # 1hr from now.
                return fhurl
            else:
                self.log.error("ERROR: _fetchhost: returned string didn't match aud. We got {0}".format(html))
                return None
        else:  # we have a host and it's under the cache time.
            return self.fetchhost

    def _fetchgames(self):
        """Return the games.txt data."""

        url = self._fetchhost()  # grab the host to check.
        if not url:  # didn't get it back.
            self.log.error("ERROR: _fetchgames broke on _fetchhost()")
            return None
        else:  # we got fetchhost. create the url.
            url = "%s/mlb/games.txt" % (url)
        # now we try and fetch the actual url with data.
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: _fetchgames: could not fetch {0} :: {1}".format(url))
            return None
        # now turn the "html" into a list of dicts.
        newgames = self._txttodict(html)
        if not newgames:  # no new games for some reason.
            return None
        else:  # return newgames.
            return newgames

    def _txttodict(self, txt):
        """Games game lines from fetchgames and turns them into a list of dicts."""

        lines = txt.splitlines()
        games = {}
        # iterate over.
        for i, line in enumerate(lines):
            if line.startswith('g|'):  # only games.
                concatline = "%s|%s" % (line, lines[i+1])  # +o|gid
                cclsplit = concatline.split('|')  # split.
                mlbid = int(cclsplit[1])  # key.
                t = {}  # dict to put all values in.
                t['awayt'] = cclsplit[4]
                t['homet'] = cclsplit[5]
                t['status'] = cclsplit[6]
                t['start'] = int(cclsplit[8])
                t['inning'] = int(cclsplit[9])
                t['awayscore'] = cclsplit[10]
                t['awayhits'] = cclsplit[11]
                t['awaypit'] = cclsplit[13]
                t['homescore'] = cclsplit[16]
                t['homehits'] = cclsplit[17]
                t['homepit'] = cclsplit[19]
                t['gameid'] = cclsplit[30]
                games[mlbid] = t
        # process if we have games or not.
        if len(games) == 0:  # no games.
            self.log.error("ERROR: _txttodict: no games found.")
            self.log.error("ERROR: _txttodict: {0}".format(txt))
            self.log.info("ERROR: _txttodict: I found no games so I am backing off 1 hour.")
            self.nextcheck = self._utcnow()+3600
            return None
        else:
            return games

    def _teamrecords(self):
        """Fetch the table of team records for when a game begins."""

        url = self._fetchhost()  # grab the host to check.
        if not url:  # didn't get it back.
            self.log.error("ERROR: _teamrecords broke on _fetchhost()")
            return None
        else:  # we got fetchhost. create the url.
            url = "%s/mlb/teams.txt" % (url)
        # now we try and fetch the actual url with data.
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: _teamrecords: could not fetch {0} :: {1}".format(url))
            return None
        # now split the lines.
        lines = html.splitlines()
        if len(lines) == 0:
            self.log.error("ERROR: _teamrecords could not find any lines in URL.")
            return None
        # dict to store everything in.
        teamlines = {}
        for line in lines:
            if line.startswith('t'):  # only t lines.
                s = line.split('|')
                #teamlines[s[1]] = "({0}-{1}) (Home: {2}-{3}) (Away {4}-{5})".format(s[6], s[7], s[10], s[11], s[8], s[9])
                teamlines[s[1]] = "{0}-{1}".format(s[6], s[7])  # make our dict with records.
        # last, make sure we found stuff.
        if len(teamlines) == 0:
            self.log.error("ERROR: _teamrecords something broke making the dict of team records.")
            return None
        else:  # everything worked
            return teamlines

    def _pitchers(self):
        """Fetch pitcher statlines."""

        url = self._fetchhost()  # grab the host to check.
        if not url:  # didn't get it back.
            self.log.error("ERROR: _pitchers broke on _fetchhost()")
            return None
        else:  # we got fetchhost. create the url.
            url = "%s/mlb/stats.txt" % (url)
        # now we try and fetch the actual url with data.
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: _teamrecords: could not fetch {0} :: {1}".format(url, e))
            return None
        # now split the lines
        lines = html.splitlines()
        if len(lines) == 0:
            self.log.error("ERROR: _teamrecords could not find any lines in URL.")
            return None
        # store in dict so we can access via key.
        pitchers = {}
        for line in lines:
            if line.startswith('k'):  # only k lines.
                s = line.split('|')
                # we calculate ERA below.
                ip = float(s[2])
                er = int(s[7])
                if ip == "0.0":  # special case if ip is 0.
                    era = float("0.00")  # would get divby0 error otherwise.
                elif er == 0:  # special case if er is 0.
                    era = float("0.00")  # would get divby0 also.
                else:  # calculate it if we have otherwise.
                    era = 9*(er/ip)  # ERA = 9 × (ER/IP)
                # create statline.
                statline = "{0}-{1}, {2:.2f}".format(s[8], s[9], era)
                # make the dict.
                pitchers[s[1]] = {'era': statline, 'saves':s[10]}
        # make sure we have something to return.
        if len(pitchers) == 0:
            return None
        else:  # return dict.
            return pitchers

    def _yahoofinal(self, gid):
        """Handle final event messaging.."""

        url = self._fetchhost()  # grab the host to check.
        if not url:  # didn't get it back.
            self.log.error("ERROR: _yahoofinal broke on _fetchhost()")
            return None
        else:  # we got fetchhost. create the url.
            url = "%s/mlb/games.txt" % (url)
        # now we grab the url.
        html = self._httpget(url)
        if not html:  # bail if we have nothing.
            self.log.error("ERROR: _yahoofinal. I could not fetch for gid: %s" % gid)
            return None
        # now that we have the html, try and find the line we need.
        endgame = re.search('^(z\|'+gid+'\|.*?)$', html, re.M|re.S)
        if not endgame:  # bail if we don't have it.
            self.log.error("ERROR: _yahoofinal looking for endgame but got: %s" % endgame)
            return None
        # we do have endgame regex so lets process it.
        endline = endgame.group(1)
        fields = re.search('^z\|\d+\|(?P<losing>\d+)\|(?P<winning>\d+)\|(?P<save>\d+)$', endline)
        if not fields:  # if, for some reason, the fields don't match.
            self.log.error("ERROR: _yahoofinal looking for fields in: %" % (endline))
            return None
        # we do have fields, so lets process them and translate into players.
        if fields:  # fields->pids.
            losing = self._yahooplayerwrapper(fields.groupdict()['losing'])
            winning = self._yahooplayerwrapper(fields.groupdict()['winning'])
            if fields.groupdict()['save'] != '0':  # if save is not 0 (ie: no save) so we grab it.
                save = self._yahooplayerwrapper(fields.groupdict()['save'])
            else:  # save was 0. (no Save.)
                save = None
            # lets decorate up the pitching with ERA + SAVE records.
            pr = self._pitchers()  # should return a dict.
            if pr:  # only manip if we get this back.
                if str(fields.groupdict()['losing']) in pr:  # check for key membership.
                    losing = "{0}({1})".format(losing, pr[str(fields.groupdict()['losing'])]['era'])
                if str(fields.groupdict()['winning']) in pr:  # check for key membership.
                    winning = "{0}({1})".format(winning, pr[str(fields.groupdict()['winning'])]['era'])
                if save:
                    if str(fields.groupdict()['save']) in pr:  # check for membership.
                        save = "{0}({1})".format(save, pr[str(fields.groupdict()['save'])]['saves'])
        # now, lets construct the actual return message.
        if losing and winning and not save:  # just L and W. no save.
            finalline = "W: {0} L: {1}".format(losing, winning)
        elif losing and winning and save:  # W/L/S.
            finalline = "W: {0} L: {1} S: {2}".format(losing, winning, save)
        else:  # something failed above.
            finalline = None
        # last, we return whatever we have.
        return finalline

    ##########################
    # YAHOO PLAYER INTERNALS #
    ##########################

    def _yahoopid(self, pid):
        """Fetch name if missing from DB."""

        try:
            url = b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbWxiL3BsYXllcnMv') + '%s' % (pid)
            html = self._httpget(url)
            if not html:
                self.log.error("ERROR: _yahoopid: Could not fetch {0}".format(url))
                return None
            soup = BeautifulSoup(html)
            pname = soup.find('li', attrs={'class':'player-name'}).getText().encode('utf-8').strip()
            self.log.info("_yahoopid: We need to add PID: {0} as {1}".format(pid, pname))
            return "{0}".format(pname)
        except Exception, e:
            self.log.error("ERROR: _yahoopid :: {0} :: {1}".format(pid, e))
            return None

    def _yahooplayer(self, pid):
        """Handle the conversion between PID and name."""

        # first, look for the player in the db by id.
        dbpath = os.path.abspath(os.path.dirname(__file__)) + '/db/players.db'
        with sqlite3.connect(dbpath) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM players WHERE id=?", (pid,))
                row = cursor.fetchone()
        # handle string/value.
        if row:  # we have player in the db.
            pname = row[0].encode('utf-8')
        else:  # no player in the db.
            pname = self._yahoopid(pid)  # try yahoo fetch.
            if not pname:  # we did not get back a pid.
                pname = "None"  # need to yield something.
        # return.
        return pname

    def _yahooplayerwrapper(self, pid):
        """Wrapper for scoring events where we must return a string instead of None."""

        pname = self._yahooplayer(pid)  # fetch.
        if not pname:  # get None back.
            pname = "player"
        # return
        return pname

    ##########################
    # SCORING EVENT HANDLING #
    ##########################

    def _runmatchtext(self, txt):
        """This takes a non-hr scoring event and parses the number of runs in it."""

        # NEED TO ADD
        # ERROR 2013-08-02T22:31:50 ERROR: _runmatchtext could not parse: scored on [8592]'s fielding error, [8874] out at second
        # ERROR 2013-08-06T23:08:47 ERROR: _runmatchtext could not parse: scored on [8578]'s fielding error
        # ERROR 2013-08-11T14:24:25 ERROR: _runmatchtext could not parse: scored on [9015]'s fielding error
        # ERROR 2013-08-17T20:58:25 ERROR: _runmatchtext could not parse: scored on [8991]'s throwing error
        # ERROR 2013-08-21T20:11:04 ERROR: _runmatchtext could not parse: scored, [7710] to second on [9007]'s fielding error
        # ERROR 2013-08-23T21:05:10 ERROR: _runmatchtext could not parse: inside the park home run to shallow left
        # ERROR 2013-08-28T21:15:57 ERROR: _runmatchtext could not parse: scored, [7511] to third on [7628]'s throwing error
        # ERROR 2013-08-31T20:24:27 ERROR: _runmatchtext could not parse: scored, [9268] to second on [9115]'s fielding error
        # ERROR 2013-09-04T16:34:45 ERROR: _runmatchtext could not parse: scored on [7276]'s throwing error
        # ERROR 2013-09-13T20:22:15 ERROR: _runmatchtext could not parse: inside the park home run to deep right center

        scoredregex = re.compile(r'(?P<three>\[\d+]\, \[\d+\] and \[\d+\] scored)|(?P<two>\[\d+\] and \[\d+\] scored)|(?P<one>(\[\d+\] scored))')
        sruns = scoredregex.search(txt)
        # regex or not.
        if sruns:  # if regex matches, figure out runs.
            if sruns.group('one'):
                runs = 1
            elif sruns.group('two'):
                runs = 2
            elif sruns.group('three'):
                runs = 3
        else:  # we didn't match so just say one run and log error.
            self.log.error("ERROR: _runmatchtext could not parse: {0}".format(txt))
            runs = 1
        # return
        return runs

    def _gameevfetch(self, gid):
        """Handles scoring event parsing for output."""

        url = self._fetchhost()  # grab the host to check.
        if not url:  # didn't get it back.
            self.log.error("ERROR: _fetchgames broke on _fetchhost()")
            return None
        else:  # we got fetchhost. create the url.
            url = "%s/mlb/plays-%s.txt" % (url, gid)
        # now do our http fetch.
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: Could not gameevfetch: {0} :: {1}".format(gid, e))
            return None
        # process the lines.
        lines = html.splitlines()  # split on \n.
        scorelines = []  # put matching lines into list.
        for line in lines:  # iterate over each.
            if line.startswith('s'):  # only scoring.
                linesplit = line.split('|')  # split line.
                scorelines.append(linesplit)  # append.
        # we now go about processing that last line.
        if len(scorelines) == 0:  # make sure we have/found events.
            return None
        else:  # now grab the last and process.
            lastline = scorelines[-1]  # grab the last item in scorelines list.
            ev = lastline[6]  # event is always at 6.
            # our approach below is to split the event by two parts. the first is always the RBI player.
            # the second is mixed: it's either a 'scoring' event or a homerun. we regex each.
            # line handles splitting these into either. sregex handles different scoring events using named groups.
            # scored handles how many runs were scored in a non-homerun event.
            # NEED TO ADD:
            lineregex = re.compile(r'^\[(?P<p>\d+)\]\s((?P<h>homered.*?)|(?P<s>.*?))$')
            sregex = re.compile(r"""
                                (  # START.
                                (?P<single>single.*?)|
                                (?P<double>doubled.*?)|
                                (?P<triple>tripled.*?)|
                                (?P<go>((grounded.*?)|(sacrificed\sto.*?)))|
                                (?P<sf>((hit\ssacrifice.*?)|(flied\sout)))|
                                (?P<walks>walked.*?)|
                                (?P<hbp>hit\sby\spitch.*?)|
                                (?P<passed>on\spassed\sball.*?)|
                                (?P<wp>wild\spitch.*?)|
                                (?P<fe>.*?fielding\serror.*?)|
                                (?P<grd>ground\srule\sdouble.*?)|
                                (?P<fc>fielder\'s\schoice.*?)|
                                (?P<balk>.*?balk)|
                                (?P<stolehome>.*?stole\shome)|
                                (?P<itphr>inside\sthe\spark\shome\srun.*?)|
                                (?P<error>((.*?error\,.*?)|(.*?error)|(unknown\sinto.*?)))
                                )$  # END.
                                """, re.VERBOSE)
            m = lineregex.search(ev)  # this breaks up the line into player and (homerun|non-hr event)
            if not m:  # for whatever reason, if lineregex breaks, we should log it.
                self.log.error("ERROR: lineregex didn't match on: {0}".format(ev))
                return None
            else:  # lineregex worked. we have two types of matches. a homerun or not.
                if m.group('h'):  # if it was a homerun.
                    h = m.group('h')  # h is our text in the homerun.
                    runs = 1  # start with one base run.
                    r = re.findall(r'(\[\d+\])', h)  # find [#] to count runs.
                    runs += len(r)  # num of [id] is runs in the HR.
                    if runs == 1:  # solo shot.
                        rbitext = "solo homerun"
                    elif runs == 4:  # runs 4 = grandslammer.
                        rbitext = "grandslam"
                    else:  # more than a solo homerun.
                        rbitext = "{0}-run homerun.".format(runs)  # text to spit out.
                elif m.group('s'):  # non-HR scoring plays.
                    s = m.group('s')  # actual text.
                    sr = sregex.search(s)  # search the text with scoring regex.
                    if sr:  # if we match a named scoring event.
                        srmatch = "".join([k for k,v in sr.groupdict().items() if v])  # find what named dict we matched.
                        srmatchtext = sr.group(srmatch)  # grab the dict (value) with the matching text.
                        # now, we conditionally handle events based on named groups in the regex from above.
                        # it should blowup if something doesn't match, in which case I'll fix.
                        ## FIX REGEXES:
                        ## scoringregex did not match anything in [7560] hit an inside the park home run to deep right, [7939] scored
                        if srmatch in ('single', 'double', 'triple'):
                            runs = self._runmatchtext(srmatchtext)  # send the remaining text to determine runs.
                            if runs == 1:  # conditional text. RBI dobule
                                rbitext = "RBI {0}".format(srmatch)
                            else:
                                rbitext = "{0}RBI {1}".format(runs, srmatch)
                        elif srmatch == 'itphr':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "inside the park home run. {0} run(s) score".format(runs)
                        elif srmatch == 'go':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "grounds out. {0} run(s) score".format(runs)
                        elif srmatch == 'sf':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "sacrifice fly. {0} run(s) score".format(runs)
                        elif srmatch == 'walks':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "walks. {0} run scores".format(runs)
                        elif srmatch == 'stolehome':
                            rbitext = "stole home"
                        elif srmatch == 'hbp':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "hit by pitch. {0} run scores".format(runs)
                        elif srmatch == 'passed':
                            rbitext = "scored on passed ball"
                        elif srmatch == 'wp':
                            rbitext = "scored on wild pitch"
                        elif srmatch == "balk":
                            rbitext = "scored on a balk"
                        elif srmatch == 'fe':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "safe at first. {0} run(s) score".format(runs)
                        elif srmatch == 'grd':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "ground rule double. {0} run(s) score".format(runs)
                        elif srmatch == 'fc':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "fielder's choice. {0} run(s) score".format(runs)
                        elif srmatch == 'error':
                            outtext = srmatchtext.split(',')[0]  # everything before the comma.
                            outtext = re.sub('\[(\d+)\]', lambda m: self._yahooplayerwrapper(m.group(1)), outtext)  # replace.
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "{0}. {1} run(s) scores.".format(outtext, runs)
                    else:  # scoring regex did not match we output and log.
                        self.log.error("ERROR: scoringregex did not match anything in {0}".format(ev))
                        rbitext = re.sub('\[(\d+)\]', lambda m: self._yahooplayerwrapper(m.group(1)), s)  # replace [\d+] w/player.
                        rbitext = "S: {0}".format(rbitext)
                # now, we need to reconstruct things for output. p = player, inning, rbitext = text from above.
                player = self._yahooplayerwrapper(m.group('p'))  # translate player # into player.
                inning = self._inningscalc(int(lastline[3]))  # get inning from ev line.
                # Finally, return something like: T6 - player scoring event.
                return "{0} - {1} {2}".format(inning, player, rbitext)

    ##########################################
    # INTERNAL EVENT HANDLING AND FORMATTING #
    ##########################################

    def _inningscalc(self, inningno):
        """Do some math to convert int innings into human-readable."""

        if inningno%2 == 0:  # modulo.
            tb = "T"  # even = top.
        else:  # odd.
            tb = "B"  # odd = bottom.
        # to get the "inning" +1, /2, round up.
        inning = round(((inningno+1)/2), 0)  # need future/division to make this work.
        return "%s%d" % (tb, inning)  # returns stuff like T1, B3, etc.

    def _delaystart(self, ev):
        """Format a gamestring for output for going into a delay."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        inning = self._inningscalc(ev['inning'])  # translate inning.
        status = ircutils.mircColor("DELAY", 'yellow')
        msg = "{0}@{1} - {2} - {3}".format(at, ht, inning, status)
        return msg

    def _delayend(self, ev):
        """Format a gamestring for output for going into a delay."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        inning = self._inningscalc(ev['inning'])  # translate inning.
        status = ircutils.mircColor("RESUMED", 'green')
        msg = "{0}@{1} - {2} - {3}".format(at, ht, inning, status)
        return msg

    def _gameextras(self, ev):
        """Game is going to extras."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        msg = "{0} {1} @ {2} {3} - Going to extra innings.".format(at, ev['awayscore'], ht, ev['homescore'])
        return msg

    def _gamestart(self, ev):
        """Format a gamestring for output for game starting."""

        # translate home/awayteam from id -> TEAM
        at = self._teams(team=ev['awayt'])
        ht = self._teams(team=ev['homet'])
        # do the same with the pitchers.
        awaypit = self._yahooplayerwrapper(ev['awaypit'])
        homepit = self._yahooplayerwrapper(ev['homepit'])
        # next section tries to dress up team and pitcher stats.
        tr = self._teamrecords()  # should return a dict of team records.
        if tr:  # if we go get it back, manip at/ht
            if ev['awayt'] in tr:  # make sure key is in dict.
                at = "{0}({1})".format(at, tr[ev['awayt']])
            if ev['homet'] in tr:  # also make sure its in there.
                ht = "{0}({1})".format(ht, tr[ev['homet']])
        # likewise, we try and find pitcher ERA/records.
        pr = self._pitchers()  # should return a dict.
        if pr:  # only manip if we get this back.
            if ev['awaypit'] in pr:  # check for key membership.
                awaypit = "{0}({1})".format(awaypit, pr[ev['awaypit']]['era'])
            if ev['homepit'] in pr:  # check for key membership.
                homepit = "{0}({1})".format(homepit, pr[ev['homepit']]['era'])
        # now format the message for output.
        starting = ircutils.mircColor("Starting", 'green')
        if awaypit and homepit:
            msg = "{0}@{1} - {2} vs {3} - T1 - {4}".format(at, ht,  awaypit, homepit, starting)
        else:
            msg = "{0}@{1} - T1 - {2}".format(at, ht, starting)
        return msg

    def _gameend(self, ev):
        """Format a gamestring for output when game ends."""

        # grab teams first.
        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        # bold the winner.
        if int(ev['awayscore']) > int(ev['homescore']):  # away won.
            teamline = "{0} {1} @ {2} {3}".format(ircutils.bold(at), ircutils.bold(ev['awayscore']), ht, ev['homescore'])
        elif int(ev['homescore']) > int(ev['awayscore']):  # home won.
            teamline = "{0} {1} @ {2} {3}".format(at, ev['awayscore'], ircutils.bold(ht), ircutils.bold(ev['homescore']))
        else:  # this should never happen but we do it to prevent against errors.
            teamline = "{0} {1} @ {2} {3}".format(at, ev['awayscore'], ht, ev['homescore'])
        # handle the inning and format it red, so it's like F/9.
        inning = ircutils.mircColor("F/%.0f" % (round(((ev['inning']+1)/2), 0)), 'red')
        # try and grab the finalline. use if it works.
        finalline = self._yahoofinal(ev['gameid'])
        # prep output.
        if finalline:  # we got finalline back and working.
            msg = "{0} - {1} - {2}".format(inning, teamline, finalline)
        else:  # something broke with finalline.
            self.log.error("ERROR: _gameend :: Could not _yahoofinal for {0}".format(ev['gameid']))
            msg = "{0} - {1}".format(inning, teamline)
        return msg

    def _gamescore(self, ev):
        """Format an event for outputting a scoring event."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        # now try and fetch the "scoring event"
        gameev = self._gameevfetch(ev['gameid'])
        if gameev:  # if it works and we get something back
            msg = "{0} {1} @ {2} {3} - {4}".format(at, ev['awayscore'], ht, ev['homescore'], gameev)
        else:  # gameev failed.
            self.log.error("ERROR: _gamescore :: Could not _gamevfetch for {0}".format(ev['gameid']))
            msg = "{0} {1} @ {2} {3}".format(at, ev['awayscore'], ht, ev['homescore'])
        return msg

    def _gamenohit(self, ev, pid):
        """Formats a player-id for a no-hitter."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        inning = self._inningscalc(ev['inning'])  # translate inning.
        player = self._yahooplayer(pid)  # try to fetch playername.
        # figure out output.
        if player:  # if we get player back.
            message = "{0}@{1} - {2} - {3} {4}".format(at, ht, inning, ircutils.bold(player), ircutils.bold("has a no hitter going."))
        else:  # no player.
            message = "{0}@{1} - {2} - {3}".format(at, ht, inning, ircutils.bold("pitcher has a no hitter going."))

        return message

    #############################
    # PUBLIC CHANNEL OPERATIONS #
    #############################

    def hardballstart(self, irc, msg, args):
        """
        start or restart the Hardball timer and live reporting.
        """

        def checkhardballcron():
            self.checkhardball(irc)
        try:
            schedule.addPeriodicEvent(checkhardballcron, self.registryValue('checkInterval'), now=False, name='checkhardball')
        except AssertionError:
            irc.reply("The hardball checker was already running.")
        else:
            irc.reply("Hardball checker started.")

    hardballstart = wrap(hardballstart, [('checkCapability', 'admin')])

    def hardballstop(self, irc, msg, args):
        """
        start or restart the Hardball timer and live reporting.
        """

        try:
            schedule.removeEvent('checkhardball')
        except KeyError:
            irc.reply("The hardball checker was not running.")
        else:
            irc.reply("Hardball checker stopped.")

    hardballstop = wrap(hardballstop, [('checkCapability', 'admin')])

    def hardballchannel(self, irc, msg, args, op, optchannel, optarg):
        """<add|list|del> <#channel> <ALL|TEAM>

        Add or delete team(s) from a specific channel's output.
        Use team abbreviation for specific teams or ALL for everything. Can only specify one at a time.
        Ex: add #channel1 ALL OR add #channel2 NYY OR del #channel1 ALL OR list
        """

        # first, lower operation.
        op = op.lower()
        # next, make sure op is valid.
        validop = ['add', 'list', 'del']
        if op not in validop:  # test for a valid operation.
            irc.reply("ERROR: '{0}' is an invalid operation. It must be be one of: {1}".format(op, " | ".join([i for i in validop])))
            return
        # if we're not doing list (add or del) make sure we have the arguments.
        if op != 'list':
            if not optchannel or not optarg:  # add|del need these.
                irc.reply("ERROR: add and del operations require a channel and team. Ex: add #channel NYY OR del #channel NYY")
                return
            # we are doing an add/del op.
            optchannel, optarg = optchannel.lower(), optarg.upper()
            # make sure channel is something we're in
            if op == 'add':  # check for channel on add only.
                if optchannel not in irc.state.channels:
                    irc.reply("ERROR: '{0}' is not a valid channel. You must add a channel that we are in.".format(optchannel))
                    return
            # test for valid team now.
            testarg = self._validteam(team=optarg)
            if not testarg:  # invalid arg(team)
                irc.reply("ERROR: '{0}' is an invalid team/argument. Must be one of: {1}".format(optarg, " | ".join(sorted(self._validteam().keys()))))
                return
        # main meat part.
        # now we handle each op individually.
        if op == 'add':  # add output to channel.
            teamid = self._teamnametoid(optarg)  # validated above.
            self.channels.setdefault(optchannel, set()).add(teamid)  # add it.
            self._savepickle()  # save.
            irc.reply("I have added {0} into {1}".format(optarg, optchannel))
        elif op == 'list':  # list channels
            if len(self.channels) == 0:  # no channels.
                irc.reply("ERROR: I have no active channels defined. Please use the hardballchannel add operation to add a channel.")
            else:   # we do have channels.
                for (k, v) in self.channels.items():  # iterate through and output
                    irc.reply("{0} :: {1}".format(k, " | ".join([self._teams(team=q) for q in v])))
        elif op == 'del':  # delete an item from channels.
            if optchannel in self.channels:  # make sure channel is in self.channels.
                teamid = self._teamnametoid(optarg)
                if teamid in self.channels[optchannel]:  # id is already in.
                    self.channels[optchannel].remove(teamid)  # remove it.
                    if len(self.channels[optchannel]) == 0:  # none left.
                        del self.channels[optchannel]  # delete the channel key.
                    self._savepickle()  # save it.
                    irc.reply("I have successfully removed {0} from {1}".format(optarg, optchannel))
                else:
                    irc.reply("ERROR: I do not have {0} in {1}".format(optarg, optchannel))
            else:  # channel is not in self.channels.
                irc.reply("ERROR: I do not have {0} in {1}".format(optarg, optchannel))

    hardballchannel = wrap(hardballchannel, [('checkCapability', 'admin'), ('somethingWithoutSpaces'), optional('channel'), optional('somethingWithoutSpaces')])

    #def mlbgames(self, irc, msg, args):
    def checkhardball(self, irc):
        """Main handling function."""

        # next, before we even compare, we should see if there is a backoff time.
        if self.nextcheck:  # if present. should only be set when we know something in the future.
            utcnow = self._utcnow()  # grab UTC now.
            if self.nextcheck > utcnow:  # we ONLY abide by nextcheck if it's in the future.
                return  # bail.
            else:  # we are past when we should be holding off checking.
                self.log.info("checkhardball: past nextcheck time so we're resetting it.")
                self.nextcheck = None  # reset nextcheck and continue.
        # first, we need a baseline set of games.
        if not self.games:  # we don't have them if reloading.
            self.log.info("checkhardball: I do not have any games. Fetching initial games.")
            self.games = self._fetchgames()
        # verify we have a baseline.
        if not self.games:  # we don't. must bail.
            self.log.error("checkhardball: after second try, I could not get self.games.")
            return
        else:  # we have games. setup the baseline stuff.
            games1 = self.games  # games to compare from.
        # now, we must grab new games. if something goes wrong or there are None, we bail.
        games2 = self._fetchgames()
        if not games2:  # if something went wrong, we bail.
            self.log.error("checkhardball: I was unable to get new games2.")
            return

        # main part/main money in the loop. we compare games1 (old) vs. games2 (new) by keys.
        # looking for differences (events). each event is then handled properly.
        for (k, v) in games1.items():  # iterate through self.games.
            if k in games2:  # match up keys because we don't know the frequency of the games/list changing.
                # scoring change events.
                if (v['awayscore'] != games2[k]['awayscore']) or (v['homescore'] != games2[k]['homescore']):
                    # bot of 9th or above. (WALKOFF) (inning = 17+ (Bot 9th), homescore changes and is > than away.
                    if ((int(games2[k]['inning']) > 16) and (v['homescore'] != games2[k]['homescore']) and (int(games2[k]['homescore']) > int(games2[k]['awayscore']))):
                        message = "{0} - {1}".format(self._gamescore(games2[k]), ircutils.bold(ircutils.underline("WALK-OFF")))
                        self._post(irc, v['awayt'], v['homet'], message)
                    else:  # regular scoring event.
                        message = self._gamescore(games2[k])
                        self._post(irc, v['awayt'], v['homet'], message)
                # game is going to extras.
                if ((v['inning'] == 17) and (games2[k]['inning'] == 18)):  # post on inning change ONLY.
                    message = self._gameextras(games2[k])
                    self._post(irc, v['awayt'], v['homet'], message)
                # game status change.
                if (v['status'] != games2[k]['status']):  # F = finished, O = PPD, D = Delay, S = Future
                    if ((v['status'] == 'S') and (games2[k]['status'] == 'P')):  # game starts.
                        self.log.info("{0} is starting.".format(k))
                        message = self._gamestart(games2[k])
                        self._post(irc, v['awayt'], v['homet'], message)
                    elif ((v['status'] == "P") and (games2[k]['status'] == 'F')):  # game finishes.
                        self.log.info("{0} is ending.".format(k))
                        message = self._gameend(games2[k])
                        self._post(irc, v['awayt'], v['homet'], message)
                    elif ((v['status'] in ('P', 'S')) and (games2[k]['status'] == 'D')):  # game goes into delay.
                        message = self._delaystart(games2[k])
                        self._post(irc, v['awayt'], v['homet'], message)
                    elif ((v['status'] == 'D') and (games2[k]['status'] == 'P')):  # game comes out of delay.
                        message = self._delayend(games2[k])
                        self._post(irc, v['awayt'], v['homet'], message)
                # no hitter. check after top of 6th (10) inning.
                if ((v['inning'] > 9) and ((v['homehits'] == '0') or (v['awayhits'] == '0'))):
                    if (v['inning'] != games2[k]['inning']):  # post on inning change ONLY.
                        # now handle which pitcher.
                        if (games2[k]['homehits'] == '0'):  # away pitcher no-hitter.
                            message = self._gamenohit(games2[k], v['awaypit'])
                            self._post(irc, v['awayt'], v['homet'], message)
                        if (games2[k]['awayhits'] == '0'):  # home pitcher no hitter.
                            message = self._gamenohit(games2[k], v['homepit'])
                            self._post(irc, v['awayt'], v['homet'], message)

        # now that we're done checking changes, copy the new into self.games to check against next time.
        self.games = games2
        # last, before we reset to check again, we need to verify some states of games in order to set sentinel or not.
        # STATUSES: S = future, P = playing, F = final, D = delay
        # first, we grab all the statuses in newgames (games2)
        gamestatuses = set([v['status'] for (k, v) in games2.items()])
        # next, check what the statuses of those games are and act accordingly.
        if (('D' in gamestatuses) or ('P' in gamestatuses)):  # if any games are being played or in a delay, act normal.
            self.nextcheck = None  # set to None to make sure we're checking on normal time.
        else:  # no games that are active or in delay.
            utcnow = self._utcnow()  # grab UTC now.
            if 'S' in gamestatuses:  # we do have games in the future (could be either before the slate or after day games are done and before night ones).
                firstgametime = sorted([f['start'] for (i, f) in games2.items() if f['status'] == "S"])[0]  # get all start times with S, first (earliest).
                if firstgametime > utcnow:   # make sure it is in the future so lock is not stale.
                    self.log.info("checkhardball: we have games in the future (S) so we're setting the next check {0} seconds from now".format(firstgametime-utcnow))
                    self.nextcheck = firstgametime  # set to the "first" game with 'S'.
                else:  # firstgametime is NOT in the future. this is a problem.
                    fgtdiff = abs(firstgametime-utcnow)  # get how long ago the first game should have been.
                    if fgtdiff < 3601:  # if less than an hour ago, just basically pass. (8:01 for an 8pm game)
                        self.log.info("checkhardball: firstgametime has passed but is under an hour so we resume normal operations.")
                        self.nextcheck = None
                    else:  # over an hour so we set firstgametime an hour from now.
                        self.log.info("checkhardball: firstgametime is over an hour from now so we're going to backoff for an hour")
                        self.nextcheck = utcnow+3600
            else:  # everything is "F" (Final). we want to backoff so we're not flooding.
                self.log.info("checkhardball: no active games and I have not got new games yet, so I am holding off for 10 minutes.")
                self.nextcheck = utcnow+600  # 10 minutes from now.

Class = Hardball

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
