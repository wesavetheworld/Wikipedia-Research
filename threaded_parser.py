import xml.sax
import time
import sys
import threading
import bz2
import Queue
import os
import csv

parseQ = Queue.Queue(5)
compressQ = Queue.Queue(100000)
printQ = Queue.Queue(100000)
writeQ = Queue.Queue(100000)
l = threading.Lock()
printLock = threading.Lock()
processed=0
totalCompression = 0
totalParse = 0
totalWrite = 0
totalEnQ = 0

class PageHandler(xml.sax.handler.ContentHandler):
	def __init__(self,pageFile,revisionFile,editorFile,encoding="utf-8"):
		encodedtab = u"\t".encode(encoding)
		
		csv.register_dialect("TabDelim",delimiter=encodedtab,quoting=csv.QUOTE_NONE,escapechar="\\")
		
		self.editors = {}
		
		self.pageOutputFields=["id","title","namespace","redirect"]
		self.pageWriter = csv.DictWriter(pageFile,fieldnames=self.pageOutputFields,\
										restval="",extrasaction='ignore',dialect="TabDelim")
		
		
		
		self.revisionOutputFields=["id","pageid","ed_id","ed_username","minor","timestamp","comment"]
		self.revisionWriter = csv.DictWriter(revisionFile,fieldnames=self.revisionOutputFields,\
											restval="",extrasaction='ignore',dialect="TabDelim")
		
		self.editorFile = editorFile
		self.attrs = {}
		self.revattrs = {}
		self.inRevision = False
		self.inContributor = False
		self.encoding = encoding
		self.revisionsParsed= 0
		self.articleRevisions = []

	def startElement(self, name, attributes):
		if name =="revision":
			self.inRevision = True
		elif name =="contributor":
			self.inContributor = True
		self.buffer=[]

	def characters(self, data):
		#print(type(self.buffer))
		self.buffer.append(data)
		#pass

	def endElement(self, name):
		#print "Found a",name+":",self.buffer[:50]
		self.buffer = "".join(self.buffer)
		
		if name == "page":
			self.handleEndOfPage()
		elif name == "revision":
			self.handleEndOfRevision()
		elif self.inRevision:
			self.handleTagWithinRevision(name)
		elif name == "mediawiki":
			self.makeEditorsFile()
		else:
			self.handleTagWithinPage(name)
			
		self.buffer = []
	
	def handleTagWithinPage(self,name):
		if name == "redirect":
			self.attrs["redirect"]="1"
		elif name == "title":
			#extracts namespace
			titleInfo = self.buffer.split(":")
			if len(titleInfo) == 2:
				#there is another namespace besides main
				self.attrs["namespace"] = titleInfo[0]
				self.attrs["title"] = titleInfo[1]
			else:
				self.attrs["namespace"] = "Main"
				self.attrs["title"] = titleInfo[0]
		elif name == "id":
			while len(self.buffer)<4:
				self.buffer = "0"+self.buffer
			self.attrs["id"]=self.buffer
		else:
			self.attrs[name]=self.buffer
		
	
	def handleEndOfPage(self):
		
		if "redirect" not in self.attrs.keys():
			self.attrs["redirect"]="0"
		
		encodeSpecifiedDictValues(self.attrs,self.pageOutputFields,self.encoding)
		self.pageWriter.writerow(self.attrs)
		
		output = self.generatePageXML()
		
		compressQ.put((self.attrs["id"],output))
		
		# Old way before csv was introduced
		#writeSpecifiedDictValuesToFile(self.attrs,["id","title","namespace","redirect"],self.pageFile,self.encoding)
		
		self.articleRevisions=[]
		self.attrs={}
	
	def generatePageXML(self):
		xmloutput = []
		xmloutput.append("<page id='{0}'>\n".format(self.attrs["id"]))
		for rev in self.articleRevisions:
			xmloutput.append("\t<revision id={0}>\n".format(rev[0]))
			xmloutput.append(rev[1])
			xmloutput.append("\t</revision>\n")
		xmloutput.append("</page>")
		
		st = "".join(xmloutput)
		st = st.encode(self.encoding)
		
		return st
	
	def handleEndOfRevision(self):
		
		self.inRevision = False
		self.revattrs["pageid"]=self.attrs["id"]

		if "minor" not in self.revattrs.keys():
			self.revattrs["minor"]="0"
		
		#if "comment" not in self.revattrs.keys():
		#	self.revattrs["comment"]=""
		
		encodeSpecifiedDictValues(self.revattrs,self.revisionOutputFields,self.encoding)
		self.revisionWriter.writerow(self.revattrs)
		
		#writeSpecifiedDictValuesToFile(self.revattrs,["id","pageid","ed_id","ed_username","minor","timestamp","comment"],self.revisionFile,self.encoding)
		
		if "ed_id" in self.revattrs.keys():
			self.editors[self.revattrs["ed_username"]]=self.revattrs["ed_id"]
		else:
			self.editors[self.revattrs["ed_username"]]=""
			
		if "text" in self.revattrs.keys():
			
			self.revisionsParsed+=1
			
			self.articleRevisions.append((self.revattrs["id"],self.revattrs["text"]))
			
			printQ.put("Revision {0:9} sent to compression queue.  {1:6} Revisions Processed.".format(self.revattrs["id"],self.revisionsParsed))
			
			# title = self.attrs["title"].encode(self.encoding)
			# text = self.revattrs["text"].encode(self.encoding)
			# 
			# global totalEnQ
			# sEnQ = time.time()
			# compressQ.put((title,self.revattrs["id"],text))
			# eEnQ = time.time()
			# totalEnQ += eEnQ - sEnQ
			
		self.revattrs={}
	
	def handleTagWithinRevision(self,name):
		if name == "timestamp":
			self.revattrs["timestamp"] = "{0} {1}".format(self.buffer[0:10],self.buffer[11:-1])
		elif name == "minor":
			self.revattrs["minor"]="1"
		elif name == "contributor":
			self.inContributor = False
		elif self.inContributor:
			if name == "username":
			    #self.escapeTabs()
			    self.revattrs["ed_username"] = self.buffer
			if name == "ip":
			    self.revattrs["ed_username"] = self.buffer
			else:
			    #name = "id"
			    self.revattrs["ed_id"]= self.buffer
		else:
			#self.escapeTabs()
			self.revattrs[name]=self.buffer
	
	def makeEditorsFile(self):
		printQ.put("Making Editors File...")
		encodedtab = "\t".encode(self.encoding)
		encodednewline = "\n".encode(self.encoding)
		
		for ed in self.editors.iteritems():
			st = "{0}{1}{2}{3}\n".format(ed[0],encodedtab,ed[1],encodednewline)
		#	st = st.encode(self.encoding)
			self.editorFile.write(st)
		printQ.put("Editors File Complete...")

	def escapeTabs(self):
		#changes \t to \\t for postgres

		toEscape = ["\\","\t","\n","\r","\N"]
		
		for escape in toEscape:
			self.buffer = self.buffer.replace(escape,"\\"+escape)
	
def encodeSpecifiedDictValues(dct,keylist,encoding):
	for key in keylist:
		if key in dct.keys():
			dct[key]=dct[key].encode(encoding)

class PrintThread(threading.Thread):
	def __init__(self):
		threading.Thread.__init__(self)
		self.setName("Printer")
		self.setDaemon(True)
		
	def run(self):
		while True:
			try:
				nextToPrint = printQ.get(True)
			except Queue.Empty:
				break
			else:
				print nextToPrint
				printQ.task_done()

class FileReadDecompress(threading.Thread):
	def __init__(self,path,chunksize):
		threading.Thread.__init__(self)
		self.path = path
		self.chunksize = chunksize
		self.setName("File Reader and Decompressor")
		
	def run(self):
		global totalCompression
		
		decom = bz2.BZ2Decompressor()
		
		with open(self.path) as infile:
			# for line in infile:
			# 	dec = decom.decompress(line)
			# 	parseQ.put(dec)

			data = infile.read(self.chunksize)
			while data != "":
				sdecom = time.time()
				dec = decom.decompress(data)
				edecom = time.time()
				totalCompression += edecom-sdecom
				
				
				parseQ.put(dec)
				data = infile.read(self.chunksize)

class ParseThread(threading.Thread):
	def __init__(self,pagefile,revfile,edfile):
		threading.Thread.__init__(self)
		self.pagefile = open(pagefile,'w')
		self.revfile = open(revfile,"w")
		self.edfile = open(edfile,"w")
		self.setName("Parser")
		
	def run(self):
		self.runParser()
		
	def runParser(self):
		parser = xml.sax.make_parser()
		handler = PageHandler(self.pagefile,self.revfile,self.edfile)
		parser.setContentHandler(handler)
		
		while True:
			try:
				printQ.put("Parser Wating...")
				p = parseQ.get(True,10)
				printQ.put("Parser Got Work..")
				sparse = time.time()
				parser.feed(p)
				eparse = time.time()
				
				global totalParse
				totalParse += eparse-sparse
				#printQ.put("Another 10MB Parsed!")
				
			except Queue.Empty:
				[f.close() for f in [self.pagefile,self.revfile,self.edfile]]
				printQ.put("{0} has had nothing to do for 10 seconds... terminating.".format(self.getName()))
				break
			else:
				parseQ.task_done()
		
class FileWrite(threading.Thread):
	def __init__(self):
		threading.Thread.__init__(self)
		self.setName("File Writer")
		self.written=0
		
	def run(self):
		while True:
			try:
				nextFile = writeQ.get(True,5)
			except Queue.Empty:
				printQ.put("{0} has had nothing to do for five seconds... terminating.".format(self.getName()))
				break
			else:
				# nextFile[0] is write path
				# nextFile[1] is file contents
				self.procFile(nextFile[0],nextFile[1])
				
	
	def procFile(self,path,content):
		f = open(path,"w")
		f.write(content)
		f.close()
		writeQ.task_done()
		printQ.put("{0:40} file written to disk.  File number {1:6}.".format(path,self.written))
		
		self.written+=1
		

class FileCompress(threading.Thread):
	def __init__(self,writernum,basepath):
		threading.Thread.__init__(self)
		self.writernum = writernum
		self.basepath = basepath
		self.setName("File Compressor {0}".format(writernum))
		
	def run(self):
		global processed
		while True:
			try:
				nextFile = compressQ.get(True,5)
			except Queue.Empty:
				printQ.put("{0} has had nothing to do for five seconds... terminating.".format(self.getName()))
				break
			else:
				# nextFile[0] should be the page id
				# nextFile[1] should be XML output for the page
			
				compressed = bz2.compress(nextFile[1])

				path = "{0}/{1}/{2}/{3}.txt.bz2".format(self.basepath,nextFile[0][:2],nextFile[0][2:4],nextFile[0])
			
				writeQ.put((path,compressed))
			
				printQ.put("{0:9} file compressed by {1}.  File number {2:6}.".format(nextFile[0],self.writernum,processed))
				l.acquire()
				processed+=1
				l.release()
				
				compressQ.task_done()
				
def make100numbereddirs(basepath):
	
	for i in range(100):
		if i >= 10:
			pth = "/{0}/{1}/".format(basepath,str(i))
		else:
			pth = "/{0}/0{1}/".format(basepath,str(i))
		if not os.path.exists(pth):
		    os.makedirs(pth)

def main():
	import yappi
	
	outpath = "/wikigroup/testoutput"
	
	make100numbereddirs(outpath)
	
	for i in range(100):
		if i >= 10:
			make100numbereddirs("{0}/{1}".format(outpath,i))
		else:
			make100numbereddirs("{0}/0{1}".format(outpath,i))
	
	start = time.time()

	PrintThread().start()
	
	path = "/wikigroup/enwiki-latest-pages-articles1.xml.bz2"
	
	# yappi.start()
	
	FileReadDecompress(path,10000000).start()
	ParseThread("pages.dat","revisions.dat","editors.dat").start()
	
	for i in range(3):
		FileCompress(i,outpath).start()
	
	#for i in range(3):
	FileWrite().start()
	
	time.sleep(5)
	
	parseQ.join()
	print "ParseQ Empty"
	compressQ.join()
	print "CompressQ Empty"
	writeQ.join()
	print "WriteQ Empty"
	
	# stats = yappi.get_stats()
	# for stat in stats:
	# 	print stat
	# yappi.stop()
	# 
	
	print " Runtime: " +str(time.time()-start) +" seconds."
	global totalCompression
	global totalParse
	global totalWrite
	global totalEnQ
	print "Compression Took {0} seconds".format(totalCompression)
	print "Parsing Took {0} secconds".format(totalParse)
	print "Writing Took {0} seconds".format(totalWrite)
	print "EnQ Took {0} seconds".format(totalEnQ)

main()
