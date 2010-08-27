
import os, sys, plugins, shutil, socket, subprocess
from ndict import seqdict

class SetUpTrafficHandlers(plugins.Action):
    def __init__(self, record):
        self.record = record
        self.trafficServerProcess = None
        libexecDir = plugins.installationDir("libexec")
        self.trafficFiles = self.findTrafficFiles(libexecDir)
        self.siteCustomizeFile = os.path.join(libexecDir, "sitecustomize.py")
        self.trafficPyModuleFile = os.path.join(libexecDir, "traffic_pymodule.py")
        self.trafficServerFile = os.path.join(libexecDir, "traffic_server.py")
        
    def findTrafficFiles(self, libexecDir):
        files = [ os.path.join(libexecDir, "traffic_cmd.py") ]
        if os.name == "nt":
            files.append(os.path.join(libexecDir, "traffic_cmd.exe"))
        return files

    def __call__(self, test):
        pythonCoverage = test.hasEnvironment("COVERAGE_PROCESS_START")
        if test.app.usesTrafficMechanism() or pythonCoverage:
            replayFile = test.getFileName("traffic")
            serverActive = self.record or replayFile
            if serverActive or pythonCoverage:
                self.setUpIntercepts(test, replayFile, serverActive, pythonCoverage)

    def setUpIntercepts(self, test, replayFile, serverActive, pythonCoverage):
        interceptDir = test.makeTmpFileName("traffic_intercepts", forComparison=0)
        pyAttributeIntercepts = self.getPythonPartialIntercepts(test)
        pathVars = self.makeIntercepts(test, interceptDir, pyAttributeIntercepts, serverActive, pythonCoverage)
        if serverActive:
            self.trafficServerProcess = self.makeTrafficServer(test, replayFile, pyAttributeIntercepts)
            address = self.trafficServerProcess.stdout.readline().strip()
            test.setEnvironment("TEXTTEST_MIM_SERVER", address) # Address of TextTest's server for recording client/server traffic
            
        for pathVar in pathVars:
            # Change test environment to pick up the intercepts
            test.setEnvironment(pathVar, interceptDir + os.pathsep + test.getEnvironment(pathVar, ""))

    def makeArgFromDict(self, dict):
        args = [ key + "=" + "+".join(val) for key, val in dict.items() if key ]
        return ",".join(args)
        
    def makeTrafficServer(self, test, replayFile, pyAttributeIntercepts):
        recordFile = test.makeTmpFileName("traffic")
        recordEditDir = test.makeTmpFileName("file_edits", forComparison=0)
        cmdArgs = [ sys.executable, self.trafficServerFile, "-t", test.getRelPath(),
                    "-r", recordFile, "-F", recordEditDir, "-l", os.getenv("TEXTTEST_PERSONAL_LOG") ]
        if not self.record:
            cmdArgs += [ "-p", replayFile ]
            replayEditDir = test.getFileName("file_edits")
            if replayEditDir:
                cmdArgs += [ "-f", replayEditDir ]

        if test.getConfigValue("collect_traffic_use_threads") != "true":
            cmdArgs += [ "-s" ]
            
        filesToIgnore = test.getCompositeConfigValue("test_data_ignore", "file_edits")
        if filesToIgnore:
            cmdArgs += [ "-i", ",".join(filesToIgnore) ]

        environmentDict = test.getConfigValue("collect_traffic_environment")
        if environmentDict:
            cmdArgs += [ "-e", self.makeArgFromDict(environmentDict) ]

        pyModuleIntercepts = test.getConfigValue("collect_traffic_py_module")
        if pyModuleIntercepts:
            cmdArgs += [ "-m", ",".join(pyModuleIntercepts) ]

        if pyAttributeIntercepts:
            cmdArgs += [ "-P", ",".join(pyAttributeIntercepts.keys()) ]
            
        asynchronousFileEditCmds = test.getConfigValue("collect_traffic").get("asynchronous")
        if asynchronousFileEditCmds:
            cmdArgs += [ "-a", ",".join(asynchronousFileEditCmds) ]

        return subprocess.Popen(cmdArgs, env=test.getRunEnvironment(), universal_newlines=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    
    def makeIntercepts(self, test, interceptDir, pyAttributeIntercepts, serverActive, pythonCoverage):
        pathVars = []
        pyModuleIntercepts = test.getConfigValue("collect_traffic_py_module")
        if serverActive:
            commands = self.getCommandsForInterception(test)
            for cmd in commands:
                self.intercept(interceptDir, cmd, self.trafficFiles, executable=True)
        
            if len(commands) and os.name == "posix":
                # Intercepts on Windows go directly into the sandbox so they can take advantage of the
                # "current working directory beats all" rule there and also intercept things that ignore PATH
                # (like Java)
                pathVars.append("PATH")

            for moduleName in pyModuleIntercepts:
                self.interceptPythonModule(moduleName, interceptDir)

            if len(pyAttributeIntercepts) > 0:
                self.interceptPythonAttributes(pyAttributeIntercepts, interceptDir)

        useSiteCustomize = (serverActive and len(pyAttributeIntercepts) > 0) or pythonCoverage
        if useSiteCustomize:
            self.interceptOwnModule(self.siteCustomizeFile, interceptDir)
        if (serverActive and len(pyModuleIntercepts)) or useSiteCustomize:
            pathVars.append("PYTHONPATH")
        return pathVars

    def getPythonPartialIntercepts(self, test):
        partialIntercept = seqdict()
        for fullAttrName in test.getConfigValue("collect_traffic_py_attributes"):
            moduleName, attrName = fullAttrName.split(".", 1)
            partialIntercept.setdefault(moduleName, []).append(attrName)
        return partialIntercept

    def interceptPythonModule(self, moduleName, interceptDir):
        modulePath = moduleName.replace(".", "/")
        self.intercept(interceptDir, modulePath + ".py", [ self.trafficPyModuleFile ], executable=False)
        self.makePackageFiles(interceptDir, modulePath)

    def interceptOwnModule(self, moduleFile, interceptDir):
        self.intercept(interceptDir, os.path.basename(moduleFile), [ moduleFile ], executable=False)

    def interceptPythonAttributes(self, moduleInfo, interceptDir):
        self.interceptOwnModule(self.trafficPyModuleFile, interceptDir)
        # We use the "sitecustomize" hook so this works on Python programs older than 2.6
        # Should probably run the user's real one, assuming they have one
        interceptorModule = os.path.join(interceptDir, "traffic_customize.py")
        interceptorFile = open(interceptorModule, "w")
        interceptorFile.write("import traffic_pymodule\n")
        for moduleName, attributes in moduleInfo.items():
            interceptorFile.write("proxy = traffic_pymodule.PartialModuleProxy(" + repr(moduleName) + ")\n")
            interceptorFile.write("proxy.interceptAttributes(" + repr(attributes) + ")\n")
        interceptorFile.close()
    
    def makePackageFiles(self, interceptDir, modulePath):
        parts = modulePath.rsplit("/", 1)
        if len(parts) == 2:
            localFileName = os.path.join(parts[0], "__init__.py")
            fileName = os.path.join(interceptDir, localFileName)
            open(fileName, "w").close() # make an empty package file
            self.makePackageFiles(interceptDir, parts[0])

    def getCommandsForInterception(self, test):
        # This gets all names in collect_traffic, not just those marked
        # "asynchronous"! (it will also pick up "default").
        return test.getCompositeConfigValue("collect_traffic", "asynchronous")

    def intercept(self, interceptDir, cmd, trafficFiles, executable):
        interceptName = os.path.join(interceptDir, cmd)
        plugins.ensureDirExistsForFile(interceptName)
        for trafficFile in trafficFiles:
            if os.name == "posix":
                os.symlink(trafficFile, interceptName)
            elif executable:
                # Windows PATH interception isn't straightforward. Only .exe files get found.
                # Put them directly into the sandbox directory rather than the purpose-built directory:
                # that way they can also intercept stuff that is otherwise picked up directly from the registry (like Java)
                interceptName = os.path.join(os.path.dirname(interceptDir), cmd)
                extension = os.path.splitext(trafficFile)[-1]
                shutil.copy(trafficFile, interceptName + extension)
            else:
                shutil.copy(trafficFile, interceptName)


class TerminateTrafficServer(plugins.Action):
    def __init__(self, setupHandler):
        self.setupHandler = setupHandler

    def __call__(self, test):
        if self.setupHandler.trafficServerProcess:
            servAddr = test.getEnvironment("TEXTTEST_MIM_SERVER")
            if servAddr:
                self.sendTerminationMessage(servAddr)
            self.writeServerErrors(self.setupHandler.trafficServerProcess)
            self.setupHandler.trafficServerProcess = None

    def sendTerminationMessage(self, servAddr):
        sendSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host, port = servAddr.split(":")
        serverAddress = (host, int(port))
        try:
            sendSocket.connect(serverAddress)
            sendSocket.sendall("TERMINATE_SERVER\n")
            sendSocket.shutdown(2)
        except socket.error: # pragma: no cover - should be unreachable, just for robustness
            plugins.log.info("Could not send terminate message to traffic server at " + servAddr + ", seemed not to be running anyway.")
        
    def writeServerErrors(self, process):
        err = process.communicate()[1]
        if err:
            sys.stderr.write("Error from Traffic Server :\n" + err)


class ModifyTraffic(plugins.ScriptWithArgs):
    # For now, only bother with the client server traffic which is mostly what needs tweaking...
    scriptDoc = "Apply a script to all the client server data"
    def __init__(self, args):
        argDict = self.parseArguments(args, [ "script" ])
        self.script = argDict.get("script")
    def __repr__(self):
        return "Updating traffic in"
    def __call__(self, test):
        fileName = test.getFileName("traffic")
        if not fileName:
            return

        self.describe(test)
        try:
            newTrafficTexts = [ self.getModified(t, test.getDirectory()) for t in self.readIntoList(fileName) ]
        except plugins.TextTestError, e:
            print str(e).strip()
            return

        newFileName = fileName + "tmpedit"
        newFile = open(newFileName, "w")
        for trafficText in newTrafficTexts:
            self.write(newFile, trafficText) 
        newFile.close()
        shutil.move(newFileName, fileName)
        
    def readIntoList(self, fileName):
        # Copied from traffic server ReplayInfo, easier than trying to reuse it
        trafficList = []
        currTraffic = ""
        for line in open(fileName, "rU").xreadlines():
            if line.startswith("<-") or line.startswith("->"):
                if currTraffic:
                    trafficList.append(currTraffic)
                currTraffic = ""
            currTraffic += line
        if currTraffic:
            trafficList.append(currTraffic)
        return trafficList
            
    def getModified(self, fullLine, dir):
        trafficType = fullLine[2:5]
        if trafficType in [ "CLI", "SRV" ]:
            proc = subprocess.Popen([ self.script, fullLine[6:]], cwd=dir,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=os.name=="nt")
            stdout, stderr = proc.communicate()
            if len(stderr) > 0:
                raise plugins.TextTestError, "Couldn't modify traffic :\n " + stderr
            else:
                return fullLine[:6] + stdout
        else:
            return fullLine
            
    def write(self, newFile, desc):
        if not desc.endswith("\n"):
            desc += "\n"
        newFile.write(desc)

    def setUpSuite(self, suite):
        self.describe(suite)
