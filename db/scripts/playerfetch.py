#!/usr/bin/env python
import urllib2
from base64 import b64decode
from BeautifulSoup import BeautifulSoup

teams = [ 'bal', 'bos', 'chw', 'cle', 'det', 'hou',
          'kan', 'laa', 'min', 'nyy', 'oak', 'sea',
          'tam', 'tex', 'ari', 'atl', 'chc', 'cin',
          'col', 'lad', 'mia', 'mil', 'nym', 'phi',
          'pit', 'sdg', 'sfo', 'stl', 'was', 'tor' ]

for team in teams:
    url = b64decode('aHR0cDovL3Nwb3J0cy55YWhvby5jb20vbWxiL3RlYW1z') + '/%s/roster' % team
    request = urllib2.Request(url)
    html = (urllib2.urlopen(request)) #.read()
    html = html.read()
    soup = BeautifulSoup(html)
    every = soup.findAll('th', attrs={'class':'title'})
    for ever in every:
        plr = ever.find('a')
        if plr:
            pnum = plr['href'].split('/')[3]
            pname = plr.getText().encode('utf-8')
            pname = pname.split(',', 1)
            outname = "{0} {1}".format(pname[1].strip(), pname[0].strip())
            print "INSERT INTO players (id, name) VALUES  ('{0}', \"{1}\");".format(pnum, outname)
