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
from operator import itemgetter
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
            if h and d:
                page = utils.web.getUrl(url, headers=h, data=d)
            else:
                page = utils.web.getUrl(url)
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
                irc.queueMsg(ircmsgs.privmsg(postchan, message))
            except Exception as e:
                self.log.error("ERROR: Could not send {0} to {1}. {2}".format(message, postchan, e))

    ##############################
    # INTERNAL CHANNEL FUNCTIONS #
    ##############################

    def _loadpickle(self):
        """Load channel data from pickle."""

        try:
            datafile = open(conf.supybot.directories.data.dirize("Hardball.pickle"), 'rb')
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
            datafile = open(conf.supybot.directories.data.dirize("Hardball.pickle"), 'wb')
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
                 '25':'SD', '26':'SF', '27':'COL', '28':'MIA', '29':'ARI', '30':'TB'}
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

    def _fetchgames(self):
        """Return the games.txt data."""

        url = b64decode('aHR0cDovL2F1ZDEyLnNwb3J0cy5hYzQueWFob28uY29tL21sYi9nYW1lcy50eHQ=')
        headers = {"User-Agent":"Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:17.0) Gecko/20100101 Firefox/17.0"}
        try:
             html = utils.web.getUrl(url, headers=headers)
        except Exception, e:
            self.log.error("ERROR: Could not fetch {0} :: {1}".format(url, e))
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
        games = []
        # iterate over.
        for i, line in enumerate(lines):
            if line.startswith('g|'):  # only games.
                concatline = "%s|%s" % (line, lines[i+1])  # +o|gid
                cclsplit = concatline.split('|')  # split.
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
                games.append(t)
        # process if we have games or not.
        if len(games) == 0:  # no games.
            self.log.error("ERROR: No matching lines in _txttodict")
            self.log.error("ERROR: _txttodict: {0}".format(txt))
            return None
        else:
            return games

    ##########################
    # YAHOO PLAYER INTERNALS #
    ##########################

    def _yahoopid(self, pid):
        """Fetch name if missing from DB."""

        try:
            url = b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbWxiL3BsYXllcnMv') + '%s' % (pid)
            html = utils.web.getUrl(url)
            soup = BeautifulSoup(html)
            pname = soup.find('li', attrs={'class':'player-name'}).getText().encode('utf-8')
            return "{0}".format(pname.strip())
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
                pname = None  # need to yield something.
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

        url = b64decode('aHR0cDovL2F1ZDEyLnNwb3J0cy5hYzQueWFob28uY29tL21sYi8=') + 'plays-%s.txt' % (gid)
        try:  # try to fetch plays.
            html = utils.web.getUrl(url)
        except Exception, e:
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
            lineregex = re.compile(r'^\[(?P<p>\d+)\]\s((?P<h>homered.*?)|(?P<s>.*?))$')
            sregex = re.compile(r"""
                                (  # START.
                                (?P<single>single.*?)|
                                (?P<double>doubled.*?)|
                                (?P<triple>tripled.*?)|
                                (?P<go>grounded.*?)|
                                (?P<sf>hit\ssacrifice.*?)|
                                (?P<walks>walked.*?)|
                                (?P<hbp>hit\sby\spitch.*?)|
                                (?P<passed>on\spassed\sball.*?)|
                                (?P<wp>wild\spitch.*?)|
                                (?P<fe>fielding\serror.*?)|
                                (?P<grd>ground\srule\sdouble.*?)|
                                (?P<fc>fielder\'s\schoice.*?)|
                                (?P<error>.*?error\,.*?)
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
                        ## FIX
                        ## Carlos Pena reaches on a force attempt, throwing error by first baseman Mitch Moreland. 1 run scores.
                        ## [6621] safe at first on first baseman [8772]'s throwing error, [8635] scored, [8640] to second
                        ## [7746] safe at first on third baseman [8624]'s throwing error, [8968] scored, [6679] to third
                        ## Guillermo Quiroz S: reached on fielder's choice, [8795] scored
                        ## Desmond Jennings S: reached on bunt single to first, [7938] scored
                        if srmatch == 'single':
                            rbitext = "RBI {0}".format(srmatch)
                        if srmatch in ('double', 'triple'):
                            runs = self._runmatchtext(srmatchtext)  # send the remaining text to determine runs.
                            rbitext = "{0}RBI {1}".format(runs, srmatch)
                        elif srmatch == 'go':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "grounds out. {0} run(s) score".format(runs)
                        elif srmatch == 'sf':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "sacrifice fly. {0} run(s) score".format(runs)
                        elif srmatch == 'walks':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "walks. {0} run scores".format(runs)
                        elif srmatch == 'hbp':
                            runs = self._runmatchtext(srmatchtext)
                            rbitext = "hit by pitch. {0} run scores".format(runs)
                        elif srmatch == 'passed':
                            rbitext = "scored on passed ball"
                        elif srmatch == 'wp':
                            rbitext = "scored on wild pitch"
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
                            rbitext = "{0}. {1} run scores.".format(outtext, runs)
                    else:  # scoring regex did not match we output and log.
                        self.log.error("ERROR: scoringregex did not match anything in {0}".format(ev))
                        rbitext = re.sub('\[(\d+)\]', lambda m: self._yahooplayerwrapper(m.group(1)), s)  # replace [\d+] w/player.
                        rbitext = "S: {0}".format(s)
                # now, we need to reconstruct things for output. p = player, inning, rbitext = text from above.
                player = self._yahooplayerwrapper(m.group('p'))  # translate player # into player.
                inning = self._inningscalc(int(lastline[3]))  # get inning from ev line.
                # Finally, return something like: T6 - player scoring event.
                return "{0} - {1} {2}".format(inning, player, rbitext)

    ######################
    # NON-SCORING EVENTS #
    ######################

    def _yahoofinal(self, gid):
        """Handle final event stuff."""

        url = b64decode('aHR0cDovL2F1ZDEyLnNwb3J0cy5hYzQueWFob28uY29tL21sYi9nYW1lcy50eHQ=')
        html = utils.web.getUrl(url)
        if not html:  # bail if we have nothing.
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
            self.log.error("ERROR: _yahoofinal looking for fields in: %s" % (endline))
            return None
        # we do have fields, so lets process them and translate into players.
        if fields:  # fields->pids.
            losing = self._yahooplayer(fields.groupdict()['losing'])
            winning = self._yahooplayer(fields.groupdict()['winning'])
            if fields.groupdict()['save'] != '0':  # if save is not 0 (ie: no save) so we grab it.
                save = self._yahooplayer(fields.groupdict()['save'])
            else:  # save was 0. (no Save.)
                save = None
        # now, lets construct the actual return message.
        if losing and winning and not save:  # just L and W. no save.
            finalline = "W: {0} L: {1}".format(losing, winning)
        elif losing and winning and save:  # W/L/S.
            finalline = "W: {0} L: {1} S: {2}".format(losing, winning, save)
        else:  # something failed above.
            finalline = None
        # last, we return whatever we have.
        return finalline

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

    def _gamestart(self, ev):
        """Format a gamestring for output for game starting."""

        at = self._teams(team=ev['awayt'])  # translate awayteam.
        ht = self._teams(team=ev['homet'])  # translate hometeam.
        starting = ircutils.mircColor("Starting", 'green')
        # try and fetch pitchers.
        awaypit = self._yahooplayer(ev['awaypit'])
        homepit = self._yahooplayer(ev['homepit'])
        # now format the message. if we have pitchers, use.
        if awaypit and homepit:
            msg = "{0}@{1} ({2} vs. {3}) - T1 - {4}".format(at, ht,  awaypit, homepit, starting)
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
        # now we see if we can fetch our player.
        player = self._yahooplayer(pid)  # try to fetch playername.
        if player:  # if we get player back.
            message = "{0}@{1} - {2} - {0} has a no hitter going.".format(at, ht, inning, player)
        else:  # no player.
            message = "{0}@{1} - {2} - pitcher has a no hitter going.".format(at, ht, inning)

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
        Ex: #channel1 ALL OR #channel2 NYY
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
            if not optchannel and not optarg:  # add|del need these.
                irc.reply("ERROR: add and del operations require a channel and team. Ex: add #channel NYY OR del #channel NYY")
                return
            # we are doing an add/del op.
            optchannel, optarg = optchannel.lower(), optarg.upper()
            # make sure channel is something we're in
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
            teamid = self._teamnametoid(optarg)
            if teamid in self.channels[optchannel]:  # id is already in.
                self.channels[optchannel].remove(teamid)  # remove it.
                self._savepickle()  # save it.
                irc.reply("I have successfully removed {0} from {1}".format(optarg, optchannel))
            else:
                irc.reply("ERROR: I do not have {0} in {1}".format(optarg, optchannel))

    hardballchannel = wrap(hardballchannel, [('checkCapability', 'admin'), ('somethingWithoutSpaces'), optional('channel'), optional('somethingWithoutSpaces')])

    #def mlbgames(self, irc, msg, args):
    def checkhardball(self, irc):
        """Main handling function."""

        # first, we need a baseline set of games.
        if not self.games:  # we don't have them if reloading.
            self.log.info("I do not have any games. Fetching initial games.")
            self.games = self._fetchgames()
        # verify we have a baseline.
        if not self.games:  # we don't. must bail.
            self.log.error("ERROR: I do not have any games in self.games.")
            return
        else:  # setup the baseline stuff.
            games1 = self.games  # games to compare from.
            utcnow = self._utcnow()

        # next, before we even compare, we should see if there is a backoff time.
        if self.nextcheck:  # if present. should only be set when we know something in the future.
            if self.nextcheck > utcnow:  # we ONLY abide by nextcheck if it's in the future.
                return  # bail.
            else:  # we are past when we should be holding off checking.
                self.nextcheck = None  # reset nextcheck and continue.

        # now, we must grab new games. if something goes wrong or there are None, we bail.
        games2 = self._fetchgames()
        if not games2:  # if something went wrong, we bail.
            self.log.error("ERROR: I was unable to get new games.")
            return

        # before we get to the main money, we need to make sure that game1 and game2 can be compared.
        # compare the start times between the two. we can also try gameids but these look to be identical.
        i1 = set([i['start'] for i in games1])  # set for intersection.
        if not len(i1.difference([i['start'] for i in games2])) == 0:  # this is true if they are different.
            self.log.info("games1 and games2 have different gameids.")
            # to verify, we also want to make sure all 'new' games are inactive.
            if len([i for i in games2 if i['status'] == "P"]) == 0:  # no new games are active. (new games/days)
                self.log.info("No new games in games2. We'll find the first game time and reset.")
                firstgametime = sorted(games2, key=itemgetter('start'), reverse=False)[0]  # find first game in new.
                if firstgametime['start'] > utcnow:  # make sure it starts after now and is not stale.
                    self.log.info("Setting next check at: {0}".format(firstgametime['start']))
                    self.nextcheck = firstgametime['start']  # set and bail.
                    self.games = games2  # reset.
                    return  # bail.

        # main part/main money in the loop.
        # we basically compare games1(old) and games2(new), looking for differences (events)
        # each event is then handled properly.
        for i, ev in enumerate(games1):
            # scoring change events.
            if (ev['awayscore'] != games2[i]['awayscore']) or (ev['homescore'] != games2[i]['homescore']):
                # bot of 9th or above. (WALKOFF) (inning = 17+ (Bot 9th), homescore changes and is > than away.
                if ((int(games2[i]['inning']) > 16) and (ev['homescore'] != games2[i]['homescore']) and (int(games2[i]['homescore']) > int(games2[i]['awayscore']))):
                    message = "{0} - {1}".format(self._gamescore(games2[i]), ircutils.bold(ircutils.underline("WALK-OFF")))
                    self._post(irc, ev['awayt'], ev['homet'], message)
                else:  # regular scoring event.
                    message = self._gamescore(games2[i])
                    self._post(irc, ev['awayt'], ev['homet'], message)
            # game status change.
            if ev['status'] != games2[i]['status']:  # F = finished, O = PPD, D = Delay, S = Future
                if ev['status'] == 'S' and games2[i]['status'] == 'P':  # game starts.
                    message = self._gamestart(games2[i])
                    self._post(irc, ev['awayt'], ev['homet'], message)
                elif ev['status'] == "P" and games2[i]['status'] == 'F':  # game finishes.
                    message = self._gameend(games2[i])
                    self._post(irc, ev['awayt'], ev['homet'], message)
                elif (ev['status'] in ('P', 'S')) and games2[i]['status'] == 'D':  # game goes into delay.
                    message = self._delaystart(games2[i])
                    self._post(irc, ev['awayt'], ev['homet'], message)
                elif ev['status'] in 'D' and games2[i]['status'] == 'P':  # game comes out of delay.
                    message = self._delayend(games2[i])
                    self._post(irc, ev['awayt'], ev['homet'], message)
            # no hitter. check after top of 6th (10) inning.
            if ev['inning'] > 9 and (ev['homehits'] == '0' or ev['awayhits'] == '0'):
                if ev['inning'] != games2[i]['inning']:  # post on inning change ONLY.
                    # now handle which pitcher.
                    if games2[i]['homehits'] == '0':  # away pitcher no-hitter.
                        message = self._gamenohit(games2[i], ev['awaypit'])
                        self._post(irc, ev['awayt'], ev['homet'], message)
                    if games2[i]['awayhits'] == '0':  # home pitcher no hitter.
                        message = self._gamenohit(games2[i], ev['homepit'])
                        self._post(irc, ev['awayt'], ev['homet'], message)

        # last, before we reset to check again, we need to verify some states of games in order to set sentinel or not.
        # first, we grab all the statuses in newgames (games2)
        gamestatuses = [i['status'] for i in games2]
        # next, check what the statuses of those games are and act accordingly.
        #if any(z in gamestatuses for z in ('D', 'P')):  # at least one is being played or in delay. act normal.
        if ('D' in gamestatuses) or ('P' in gamestatuses):  # if any games are being played or in a delay, act normal.
            self.nextcheck = None  # set to None to make sure we're checking on normal time.
        elif 'S' in gamestatuses:  # we have games in the future.
            # this status happens when no games are being played or in delay but not all are final (ie day game and later night).
            firstgametime = sorted([f['start'] for f in games2 if f['status'] == "S"])[0]  # get all start times with S, first (earliest).
            if firstgametime > utcnow:   # make sure it is in the future so lock is not stale.
                self.nextcheck = firstgametime  # set to the "first" game with 'S'.
                self.log.info("We have games in the future (S) so we're setting the next check {0} seconds from now".format(firstgametime-utcnow))
            else:  # time is not in the future. not sure why but we bail so we're not using a stale nextcheck.
                self.nextcheck = None
                self.log.info("We have games in the future (S) but the firstgametime I got was NOT in the future".format(firstgametime))
        else:  # everything is "F" (Final). we want to backoff so we're not flooding.
            self.nextcheck = utcnow+600  # 10 minutes from now.
            self.log.info("No active games and I have not got new games yet, so I am holding off for 10 minutes.")
        # last, change self.games over to our last processed games (games2).
        self.games = games2  # change status.

    #mlbgames = wrap(mlbgames)

Class = Hardball

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
