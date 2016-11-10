#!/usr/bin/env python
from sys import exit, argv
from commands import getstatusoutput
from os.path import exists, getmtime, dirname, basename
from os import environ
from time import time, sleep
import json, threading, re
from optparse import OptionParser

def write_json(outfile, cache):
  outdir = dirname(outfile)
  if not exists(outdir): getstatusoutput("mkdir -p %s" % outdir)
  ofile = open(outfile, 'w')
  if ofile:
    ofile.write(json.dumps(cache, sort_keys=True, indent=2,separators=(',',': ')))
    ofile.close()

def read_json(infile):
  with open(infile) as json_data:
    return json.load(json_data)

def run_das_client(outfile, query, override, threshold=900, retry=5, limit=0):
  das_cmd = "das_client --format=json --limit=%s --query '%s | grep file.name | sort | unique' --retry=%s --threshold=%s" % (limit, query,retry, threshold)
  err, out = getstatusoutput(das_cmd)
  if err:
    print out
    return False
  jdata = json.loads(out)
  if (not "status" in jdata) or (jdata['status'] != 'ok') or (not "data" in jdata):
    print out
    return False
  results = {'mtime' : time(), 'files' : []}
  for item in jdata["data"]:
    if (not item["file"]) or (not 'name' in item["file"][0]): continue
    results['files'].append(item["file"][0]["name"])
  if (len(results['files'])==0) and ('site=T2_CH_CERN' in query):
    query = query.replace("site=T2_CH_CERN","").strip()
    print "Removed T2_CH_CERN restrictions and limit set to 10: %s" % query
    return run_das_client(outfile, query, override, threshold, retry, limit=10)
  if results['files'] or override:
    print "  Success '%s', found %s files." % (query, len(results['files']))
    if results['files']:
      write_json (outfile, results)
      write_json (outfile+".json", jdata)
    else:
      getstatusoutput("rm -f %s %s.json" % (outfile,outfile))
  return True

if __name__ == "__main__":
  parser = OptionParser(usage="%prog <options>")
  parser.add_option("-t", "--threshold",  dest="threshold", help="Threshold time in sec to refresh query results. Default is 86400s", type=int, default=86400)
  parser.add_option("-o", "--override",   dest="override",  help="Override previous cache requests in cache empty results are returned from das", action="store_true", default=False)
  parser.add_option("-j", "--jobs",       dest="jobs",      help="Parallel das_client queries to run. Default is equal to cpu count but max value is 8", default=-1)
  parser.add_option("-s", "--store",      dest="store",     help="Name of object store directory to store the das queries results", default=None)

  opts, args = parser.parse_args()
  if (not opts.store): parser.error("Missing store directory path to store das queries objects.")

  uqueries = {}
  query_sha = {}
  err, qout = getstatusoutput("find %s -name '*.query' -type f" % opts.store)
  for qfile in qout.split("\n"):
    sha = basename(qfile).replace(".query","")
    qs = {}
    rewrite = False
    for query in [line.rstrip('\n').strip() for line in open(qfile)]:
      if not "file " in query: continue
      if "--query " in query:
        query = query.split("--query ")[1].split("'")[1]
        rewrite = True
      query = re.sub("= ","=",re.sub(" =","=",re.sub("  +"," ",query)))
      uqueries[query] = []
      query_sha[query]=sha
      qs[query]=1
    if rewrite:
      ofile = open(qfile, 'w')
      if ofile:
        for q in qs: ofile.write("%s\n" % q)
        ofile.close()

  tqueries = len(uqueries)
  print "Found %s unique queries" % (tqueries)
  jobs = opts.jobs
  if jobs <= 0:
    e, o = getstatusoutput("nproc")
    jobs = int(o)
  if jobs>8: jobs=8
  print "Parallel jobs:", jobs

  getstatusoutput("mkdir -p %s" % opts.store)
  threads = []
  nquery = 0
  inCache = 0 
  DasSearch = 0
  for query in uqueries:
    nquery += 1
    sha = query_sha[query]
    outfile = "%s/%s/%s" % (opts.store, sha[0:2], sha)
    print "[%s/%s] Quering %s '%s'" % (nquery, tqueries, sha, query)
    if exists(outfile):
      jdata = read_json (outfile)
      dtime = time()-jdata['mtime']
      fcount = len(jdata['files'])
      if (dtime<=opts.threshold) and (fcount>0):
        uqueries[query] = jdata['files']
        print "  Found in cache with %s files (age: %s src)" % (fcount , dtime)
        inCache += 1
        continue
      elif fcount>0: print "  Refreshing as cache expired (age: %s sec)" % dtime
      else: print "  Retrying as cache with empty file list found."
    else: print "  No cache file found %s" % sha
    
    DasSearch += 1
    while True:
      threads = [t for t in threads if t.is_alive()]
      tcount = len(threads)
      if(tcount < jobs):
        print "  Searching DAS (threads: %s)" % tcount
        try:
          t = threading.Thread(target=run_das_client, args=(outfile, query, opts.override))
          t.start()
          threads.append(t)
          sleep(1)
        except Exception, e:
          print "ERROR threading das query cache: caught exception: " + str(e)
        break
      else:
        sleep(10)
  for t in threads: t.join()
  print "Total queries: %s" % tqueries
  print "Found in object store: %s" % inCache
  print "DAS Search: %s" % DasSearch