# -*- coding: utf-8 -*-
###
# Copyright (c) 2013, spline
# All rights reserved.
#
#
###
# my libs
import datetime  # time
import pytz  # time
from calendar import timegm  # time
from base64 import b64decode
import cPickle as pickle
try:
    import xml.etree.cElementTree as ElementTree
except ImportError:
    import xml.etree.ElementTree as ElementTree
import json
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
        # dupedict.
        self.dupedict = {}
        # base url.
        self.baseurl = b64decode('aHR0cDovL2dkMi5tbGIuY29t')
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

    def _httpget(self, url):
        """General HTTP resource fetcher. Pass headers via h, data via d, and to log via l."""

        l = False
        if self.registryValue('logURLs') or l:
            self.log.info(url)

        try:
            h = {"User-Agent":"Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:17.0) Gecko/20100101 Firefox/17.0"}
            page = utils.web.getUrl(url, headers=h)
            return page
        except utils.web.Error as e:
            self.log.error("ERROR opening {0} message: {1}".format(url, e))
            return None

    def _datestring(self):
        """Figure out the datestring for main GD url."""

        # now in UTC.
        now = datetime.datetime.utcnow()
        # test if we're after 1PM UTC. We do this since some games last until 4AM Eastern.
        if 0 <= now.hour <= 12:  # between 0-12, go back one day.
            base = now+datetime.timedelta(days=-1)
        else:  # regular.
            base = now
        # now figure out the year, month, day strings.
        dyear, dmonth, dday = base.strftime("%Y"), base.strftime("%m"), base.strftime("%d")
        # return as tuple.
        return (dyear, dmonth, dday)

    def _convertUTC(self, dtstring):
        """We convert our dtstrings in each game into UTC epoch seconds."""

        naive = datetime.datetime.strptime(dtstring, "%Y/%m/%d %I:%M %p")  # 2013/09/21 7:08 PM
        local = pytz.timezone("US/Eastern")  # times are localized in "Eastern"
        local_dt = local.localize(naive, is_dst=None)  # tzize dtobj.
        utc_dt = local_dt.astimezone(pytz.UTC) # convert from utc->local(tzstring).
        rtrstr = timegm(utc_dt.utctimetuple())  # return epoch seconds/
        return rtrstr

    def _utcnow(self):
        """Calculate Unix timestamp from GMT."""

        ttuple = datetime.datetime.utcnow().utctimetuple()
        return timegm(ttuple)

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
            except Exception, e:
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

        table = { '0':'ALL', # ALL.
            '139':'TB', '110':'BAL', '135':'SD', '119':'LAD', '118':'KC', '140':'TEX',
            '115':'COL', '109':'ARI', '112':'CHC', '144':'ATL', '108':'LAA', '136':'SEA',
            '133':'OAK', '142':'MIN', '147':'NYY', '137':'SF', '134':'PIT', '113':'CIN',
            '114':'CLE', '117':'HOU', '158':'MIL', '138':'STL', '120':'WSH', '146':'MIA',
            '116':'DET', '145':'CWS', '143':'PHI', '121':'NYM', '111':'BOS', '141':'TOR',
            '160':'NL', '159':'AL' # ASG.
            }
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
        """Return the games data."""

        # first, we need our year, month, date.
        (dyear, dmonth, dday) = self._datestring()
        # now construct the url.
        url = self.baseurl + '/components/game/mlb/year_%s/month_%s/day_%s/master_scoreboard.json' % (dyear, dmonth, dday)
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: _fetchgames: could not fetch {0} :: {1}".format(url))
            return None
        # we got something back.
        try:  # try and decode the JSON.
            tree = json.loads(html.decode('utf-8'))
        except Exception, e:
            self.log.error("_fetchgames :: Could not parse JSON :: {0}".format(e))
            return None
        # JSON did parse. We have to mangle a few things before we can build the dict.
        try:  # try to find the base.
            games = tree['data']['games']
        except Exception, e:
            self.log.error("_fetchgames :: Could not parse games in JSON :: {0}".format(e))
            return None
        # we have games. We have to check if there are games today.
        if ((not 'game' in games) or (len(games) == 0)):  # we found 'game', meaning there are games.
            self.log.error("_fetchgames :: I did not find 'game' in games :: I got: {0}".format(games))
            self.log.info("ERROR: _fetchgames: I found no games so I am backing off 1 hour.")
            self.nextcheck = self._utcnow()+3600
            return None
        else:  # we're good to go. last check is below due to a single vs. multiple games.
            games = games['game']
            if isinstance(games, dict):  # stupid stopgap here for single games.
                games = [games]  # add that single game dict into a list.
        # now we're ready to go.
        g = {}  # base container.
        # iterate over all entries.
        for game in games:
            t = {}  # tmp dict for each game.
            gid = game['id']  # find our ID.
            gametime = "{0} {1}".format(game['time_date'], game['hm_lg_ampm'])  # construct string.
            t['gametime'] = self._convertUTC(gametime)  # add in string as UTC time.
            t['scoringplays'] = game['game_data_directory'] + '/atv_runScoringPlays.xml'  # add in url.
            status = game['status']['ind']
            t['status'] = status  # this is the code.
            # handle conditionals for status.
            # unfortunately, mlb provides different json variables depending on the status.
            # we only need certain ones for specific conditions, so we act accordingly.
            # STATUSES: O = game just finished, F = final, S = future, P, PW = warmup, I = In Progress, PR = Rain, PY = Delayed start, DA = PPD
            if status in ("O", "F"):  # FINAL/POST GAME
                # innings
                t['inning'] = int(game['status']['inning'])
                if game['status']['top_inning'] == "N":
                    t['inningfull'] = "B{0}".format(t['inning'])
                else:  # top of the inning
                    t['inningfull'] = "T{0}".format(t['inning'])
                # PITCHING
                # winning pitching.
                t['wpitcher'] = game['winning_pitcher']['name_display_roster'].encode('utf-8')
                t['wpitcherera'] = game['winning_pitcher']['era']
                t['wpitcherwins'] = game['winning_pitcher']['wins']
                t['wpitcherlosses'] = game['winning_pitcher']['losses']
                # winning pitching.
                t['lpitcher'] = game['losing_pitcher']['name_display_roster'].encode('utf-8')
                t['lpitcherera'] = game['losing_pitcher']['era']
                t['lpitcherwins'] = game['losing_pitcher']['wins']
                t['lpitcherlosses'] = game['losing_pitcher']['losses']
                # save_pitcher
                t['spitcher'] = game['save_pitcher']['name_display_roster'].encode('utf-8')
                t['spitchersaves'] = game['save_pitcher']['saves']
                # SCORE
                t['homescore'] = int(game['linescore']['r']['home'])
                t['awayscore'] = int(game['linescore']['r']['away'])
                # HITS
                t['homehits'] = int(game['linescore']['h']['home'])
                t['awayhits'] = int(game['linescore']['h']['away'])
            elif status in ("S", "P", "PW", "PY"):  # BEFORE THE GAME.
                # PITCHING.
                # away pitching.
                t['apitcher'] = game['away_probable_pitcher']['name_display_roster'].encode('utf-8')
                t['apitcherera'] = game['away_probable_pitcher']['era']
                t['apitcherwins'] = game['away_probable_pitcher']['wins']
                t['apitcherlosses'] = game['away_probable_pitcher']['losses']
                # home pitching.
                t['hpitcher'] = game['home_probable_pitcher']['name_display_roster'].encode('utf-8')
                t['hpitcherera'] = game['home_probable_pitcher']['era']
                t['hpitcherwins'] = game['home_probable_pitcher']['wins']
                t['hpitcherlosses'] = game['home_probable_pitcher']['losses']
            elif status == "I":  # INPROGRESS.
                # innings
                t['inning'] = int(game['status']['inning'])
                if game['status']['top_inning'] == "N":
                    t['inningfull'] = "B{0}".format(t['inning'])
                else:  # top of the inning
                    t['inningfull'] = "T{0}".format(t['inning'])
                # PITCHING
                t['pitcher'] = game['pitcher']['name_display_roster'].encode('utf-8')
                t['opitcher'] = game['opposing_pitcher']['name_display_roster'].encode('utf-8')
                # SCORE
                t['homescore'] = int(game['linescore']['r']['home'])
                t['awayscore'] = int(game['linescore']['r']['away'])
                # HITS
                t['homehits'] = int(game['linescore']['h']['home'])
                t['awayhits'] = int(game['linescore']['h']['away'])
            # VARIABLES FOR ALL.
            # handle hometeam.
            t['home_loss'] = game['home_loss']
            t['home_win'] = game['home_win']
            t['hometeam'] = game['home_name_abbrev']
            t['homeid'] = game['home_team_id']
            # handle awayteam.
            t['away_loss'] = game['away_loss']
            t['away_win'] = game['away_win']
            t['awayteam'] = game['away_name_abbrev']
            t['awayid'] = game['away_team_id']
            # finally, add it to the dict.
            g[gid] = t
        # now return the dict of dicts.
        return g

    ##########################
    # SCORING EVENT HANDLING #
    ##########################

    def _gameevfetch(self, spurl):
        """Handles scoring event parsing for output."""

        # construct the url.
        url = self.baseurl + spurl
        # now do our http fetch.
        html = self._httpget(url)
        if not html:
            self.log.error("ERROR: _gameevfetch :: HTTP ERROR fetching: {0} :: {1}".format(url))
            return None
        # now lets try and parse the XML.
        try:
            tree = ElementTree.fromstring(html.decode('utf-8'))
        except Exception, e:
            self.log.error("_gameevfetch :: Could not parse XML from {0} :: {1}".format(url, e))
            return None
        # we can parse XML. Lets go and find the "last" scoring event.
        scev = tree.findall('body/eventGroup/event')
        if len(scev) == 0:  # make sure we found events...
            self.log.error("ERROR: _gameevfetch: No scoring events found at {0}".format(url))
            return None
        else: # we did find events. lets return the 'last' and clean-up the text.
            t = {}  # tmp dict.
            t['title'] = scev[-1].find('title').text.encode('utf-8')  # type of scoring play. play below.
            ev = scev[-1].find('description').text.encode('utf-8')  # find event and clean it up below.
            t['event'] = utils.str.normalizeWhitespace(ev) #(ev.split('.', 1)[0])
            #self.log.info("_gameevfetch :: {0}".format(t))
            # return the dict.
            return t

    #############################
    # PUBLIC CHANNEL OPERATIONS #
    #############################

    def hardballon(self, irc, msg, args, channel):
        """
        Re-enable hardball updates in channel.
        Must be enabled by an op in the channel scores are already enabled for.
        """

        # channel
        channel = channel.lower()
        # check if op.
        if not irc.state.channels[channel].isOp(msg.nick):
            irc.reply("ERROR: You must be an op in this channel for this command to work.")
            return
        # check if channel is already on.
        if channel in self.channels:
            irc.reply("ERROR: {0} is already enabled for hardball updates.".format(channel))
        # we're here if it's not. let's re-add whatever we have saved.
        # most of this is from _loadchannels
        try:
            datafile = open(conf.supybot.directories.data.dirize(self.name()+".pickle"), 'rb')
            try:
                dataset = pickle.load(datafile)
            finally:
                datafile.close()
        except IOError:
            irc.reply("ERROR: I could not open the hardball pickle to restore. Something went horribly wrong.")
            return
        # now check if channels is in the dataset from the pickle.
        if channel in dataset['channels']:  # it is. we're good.
            self.channels[channel] = dataset['channels'][channel]  # restore it.
            irc.reply("I have successfully restored updates to: {0}".format(channel))
        else:
            irc.reply("ERROR: {0} is not in the saved channel list. Please use cfbchannel to add it.".format(channel))

    hardballon = wrap(hardballon, [('channel')])

    def hardballoff(self, irc, msg, args, channel):
        """
        Disable hardball scoring updates in a channel.
        Must be issued by an op in a channel it is enabled for.
        """

        # channel
        channel = channel.lower()
        # check if op.
        if not irc.state.channels[channel].isOp(msg.nick):
            irc.reply("ERROR: You must be an op in this channel for this command to work.")
            return
        # check if channel is already on.
        if channel not in self.channels:
            irc.reply("ERROR: {0} is not in self.channels. I can't disable updates for a channel I don't update in.".format(channel))
            return
        else:  # channel is in the dict so lets do a temp disable by deleting it.
            del self.channels[channel]
            irc.reply("I have successfully disabled hardball updates in {0}".format(channel))

    hardballoff = wrap(hardballoff, [('channel')])

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

    #########################
    # GAME EVENT FORMATTERS #
    #########################

    def _boldleader(self, at, ats, ht, hts):
        """Helper to bold the leader."""

        # bold the winner.
        if int(ats) > int(hts): # away team winning.
            teamline = "{0} {1} @ {2} {3}".format(ircutils.bold(at), ircutils.bold(ats), ht, hts)
        elif int(hts) > int(ats): # home team winning.
            teamline = "{0} {1} @ {2} {3}".format(at, ats, ircutils.bold(ht), ircutils.bold(hts))
        else: # this should never happen but we do it to prevent against errors.
            teamline = "{0} {1} @ {2} {3}".format(at, ats, ht, hts)
        # now return
        return teamline

    def _gamestart(self, ev):
        """Handle a game starting."""

        # construct the teams w/records.
        at = "{0}({1}-{2})".format(ev['awayteam'], ev['away_win'], ev['away_loss'])
        ht = "{0}({1}-{2})".format(ev['hometeam'], ev['home_win'], ev['home_loss'])
        # construct pitching w/w-l + era.
        ap = "{0} ({1}-{2}, {3})".format(ev['apitcher'], ev['apitcherwins'], ev['apitcherlosses'], ev['apitcherera'])
        hp = "{0} ({1}-{2}, {3})".format(ev['hpitcher'], ev['hpitcherwins'], ev['hpitcherlosses'], ev['hpitcherera'])
        # rest of the string
        m = "{0} @ {1} :: {2} v. {3} :: {4}".format(at, ht, ap, hp, ircutils.mircColor("STARTING", 'green'))
        # return the string.
        return m

    def _gamefinish(self, ev):
        """Handle a game finishing."""

        finalstr = "F/{0}".format(ev['inning'])  # make final string.
        # pitching here. conditional if we have a save pitcher or not.
        wp = "{0}({1}-{2}, {3})".format(ev['wpitcher'], ev['wpitcherwins'], ev['wpitcherlosses'], ev['wpitcherera'])
        lp = "{0}({1}-{2}, {3})".format(ev['lpitcher'], ev['lpitcherwins'], ev['lpitcherlosses'], ev['lpitcherera'])
        if ev['spitcher'] == "":  # empty so no save.
            pitching = "W: {0}  L: {1}".format(wp, lp)
        else:  # have save pitcher.
            pitching = "W: {0}  L: {1}  S: {2}({3})".format(wp, lp, ev['spitcher'], ev['spitchersaves'])
        # bold the leader.
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # construct the string.
        m = "{0} :: {1} :: {2}".format(bl, pitching, ircutils.mircColor(finalstr, 'red'))
        # return.
        return m

    def _gamescore(self, ev):
        """Handles a scoring event."""

        # first, bold the leader (prefix part)
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # now try and fetch the "scoring event"
        gameev = self._gameevfetch(ev['scoringplays'])
        if gameev: # if it works and we get something back
            m = "{0} - {1} :: {2} :: {3}".format(bl, ev['inningfull'], gameev['title'], gameev['event'])
        else: # gameev failed. just print the score.
            self.log.error("ERROR: _gamescore :: Could not _gamevfetch for {0}".format(ev['id']))
            m = "{0} - {1}".format(bl, ev['inningfull'],)
        # return.
        return m

    def _extrainnings(self, ev):
        """Handles a game going to extras."""

        t = "{0} {1} @ {2} {3}".format(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        m = "{0} - {1} :: {2}".format(t, ev['inningfull'], ircutils.bold("EXTRA INNINGS"))
        # return.
        return m

    def _gamedelay(self, ev):
        """Handles game going into a delay."""

        # bold leader.
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # build string.
        m = "{0} - {1} :: {2}".format(bl, ev['inningfull'], ircutils.mircColor("DELAY", 'yellow'))
        # return
        return m

    def _gameresume(self, ev):
        """Handles game coming out of a delay."""

        # bold leader.
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # build string.
        m = "{0} - {1} :: {2}".format(bl, ev['inningfull'], ircutils.mircColor("RESUMED", 'green'))
        # return
        return m

    def _gameppd(self, ev):
        """Handles game going PPD."""

        # bold leader.
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # build string.
        m = "{0} - {1} :: {2}".format(bl, ev['inningfull'], ircutils.mircColor("PPD", 'yellow'))
        # return
        return m

    def _nohitter(self, ev, nhteam, nhpitcher):
        """Handles game with no-hitter going on."""

        # bold leader.
        bl = self._boldleader(ev['awayteam'], ev['awayscore'], ev['hometeam'], ev['homescore'])
        # now create the string.
        m = "{0} - {1} - {2}({3}) :: {4}".format(bl, ev['inningfull'], nhpitcher, nhteam, ircutils.bold("NO-HITTER GOING ON"))
        return m

    #################
    # MAIN FUNCTION #
    #################

    #def checkhardball(self, irc, msg, args):
    def checkhardball(self, irc):
        """Main handling function."""

        self.log.info("checkhardball: starting check.")

        # next, before we even compare, we should see if there is a backoff time.
        if self.nextcheck:  # if present. should only be set when we know something in the future.
            utcnow = self._utcnow()  # grab UTC now.
            if self.nextcheck > utcnow:  # we ONLY abide by nextcheck if it's in the future.
                self.log.info("checkhardball: nextcheck is in the future")
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
                # ACTIVE GAME EVENTS ONLY (INCLUDING IF GOING FINAL)
                if ((v['status'] == "I") and (games2[k]['status'] in ("I", "O", "F"))):
                    # FIRST, MAKE SURE THE GAME IS IN DUPEDICT.
                    if k not in self.dupedict:
                        self.log.info("ACTIVE GAME :: {0} is not in dupedict. Adding it.".format(k))
                        self.dupedict[k] = ""
                    # SCORING EVENTS. WE CHECK IF ITS A WALK-OFF.
                    if ((v['awayscore'] < games2[k]['awayscore']) or (v['homescore'] < games2[k]['homescore'])):
                        self.log.info("{0} should post scoring event".format(k))
                        # CHECK IF ITS A WALK-OFF.
                        if ((games2[k]['inning'] > 8) and (v['homescore'] != games2[k]['homescore']) and (games2[k]['homescore'] > games2[k]['awayscore'])):  # WO.
                            mstr = "{0} - {1}".format(self._gamescore(games2[k]), ircutils.bold(ircutils.underline("WALK-OFF")))
                        else:  # NOT A WALKOFF. IE: REGULAR SCORING EVENT.
                            mstr = self._gamescore(games2[k])
                        # POST
                        self._post(irc, v['awayid'], v['homeid'], mstr)
                    # GAME IS GOING TO EXTRAS.
                    if ((v['inning'] != games2[k]['inning']) and (games2[k]['inningfull'] == "T10")):
                        self.log.info("{0} is  going into extra innings.".format(k))
                        mstr = self._extrainnings(games2[k])
                        self._post(irc, v['awayid'], v['homeid'], mstr)
                    # NO HITTER CHECK HERE. WE ONLY CHECK FROM THE 6TH INNING AND ON.
                    if ((v['inningfull'] != games2[k]['inningfull']) and (games2[k]['inning'] > 5) and ((games2[k]['homehits'] == 0) or (games2[k]['awayhits'] == 0))):
                        self.log.info("{0} no hitter somewhere in here.".format(k))
                        # DETERMINE WHICH PITCHER HAS A NO-HITTER GOING ON.
                        if (games2[k]['homehits'] == 0):  # away no-hitter.
                            nhteam = 'awayteam'
                            nhinning = "B{0}".format(games2[k]['inning'])
                        else:  # home pitcher no-hitter.
                            nhteam = 'hometeam'
                            nhinning = "T{0}".format(games2[k]['inning'])
                        # NOW SEE IF WE JUST CHANGED TO THAT INNING SO WE DON'T SPAM NOTIFICATION.
                        # This is so we ONLY print the NH at top of inning nh pitcher is in.
                        if (games2[k]['inningfull'] == nhinning):
                            # grab our variables.
                            nhteam = games2[k][nhteam]
                            nhpitcher = games2[k]['pitcher']
                            # log the event.
                            self.log.info("{0} {1}({2}) has a no hitter going on.".format(k, nhpitcher, nhteam))
                            # create string for output.
                            mstr = self._nohitter(games2[k], nhteam, nhpitcher)
                            self._post(irc, v['awayid'], v['homeid'], mstr)
                        else:  # debug
                            self.log.info("{0} has a no hitter going on but wrong inning.".format(k))
                # GAME STATUS CHANGES (NON-ACTIVE EVENTS)
                if (v['status'] != games2[k]['status']):
                    # GAME STARTS
                    if (games2[k]['status'] == 'I'):
                        self.log.info("{0} is starting.".format(k))
                        # now put k in dupedict.
                        if k not in self.dupedict:
                            mstr = self._gamestart(v)
                            self._post(irc, v['awayid'], v['homeid'], mstr)
                            self.dupedict[k] = ""
                        else:
                            self.log.info("{0} is starting but I already had it in dupedict.".format(k))
                    # GAME FINISHES
                    elif (games2[k]['status'] in ('O', 'F')):
                        self.log.info("{0} is going Final.".format(k))
                        # now test if k is in dupedict.
                        if k in self.dupedict:  # key is in the dupedict, which is good, so we post.
                            mstr = self._gamefinish(games2[k])
                            self._post(irc, v['awayid'], v['homeid'], mstr)
                            del self.dupedict[k]  # delete the key now.
                        else:
                            self.log.info("dupedict: ERROR: {0} is not in dupedict.".format(k))
                    # GAME INTO DELAY.
                    elif (games2[k]['status'] == 'PR'):
                        self.log.info("{0} is going into a delay.".format(k))
                        mstr = self._gamedelay(games2[k])
                        self._post(irc, v['awayid'], v['homeid'], mstr)
                    # GAME COMES OUT OF A DELAY
                    elif (v['status'] == 'PR'):
                        self.log.info("{0} is coming out of a delay.".format(k))
                        mstr = self._gameresume(games2[k])
                        self._post(irc, v['awayid'], v['homeid'], mstr)
                    # GAME GOES TO PPD.
                    elif (games2[k]['status'] == 'DA'):
                        self.log.info("{0} is PPD.".format(k))
                        mstr = self._gameppd(games2[k])
                        self._post(irc, v['awayid'], v['homeid'], mstr)

        # now that we're done checking changes, copy the new into self.games to check against next time.
        self.games = games2
        # last, before we reset to check again, we need to verify some states of games in order to set sentinel or not.
        # STATUSES: O = game just finished, F = final, S = future, P, PW = warmup, I = In Progress, PR = Rain, PY = Delayed start, DA = PPD
        # first, we grab all the statuses in newgames (games2)
        gamestatuses = set([v['status'] for (k, v) in games2.items()])
        self.log.info("GAMESTATUSES: {0}".format(gamestatuses))
        # next, check what the statuses of those games are and act accordingly.
        if (('PR' in gamestatuses) or ('I' in gamestatuses) or ('P' in gamestatuses) or ('PW' in gamestatuses)):  # act normal if: rain delay, in-progress, warmups.
            self.nextcheck = None  # set to None to make sure we're checking on normal time.
        else:  # no games that are active or in delay.
            utcnow = self._utcnow()  # grab UTC now.
            if 'S' in gamestatuses:  # we do have games in the future (could be either before the slate or after day games are done and before night ones).
                firstgametime = sorted([f['gametime'] for (i, f) in games2.items() if f['status'] == "S"])[0]  # get all start times with S, first (earliest).
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
            else:  # everything is "O" (Over) or "F" (Final) or "DA" (PPD). we want to backoff so we're not flooding.
                self.log.info("checkhardball: no active games and I have not got new games yet, so I am holding off for 10 minutes.")
                self.nextcheck = utcnow+600  # 10 minutes from now.

    #checkhardball = wrap(checkhardball)

Class = Hardball

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
