
""" Action for running a test locally or on a remote machine """

import plugins, os, logging, subprocess, sys, signal
from time import sleep
from threading import Lock, Timer
from jobprocess import killSubProcessAndChildren

plugins.addCategory("killed", "killed", "were terminated before completion")

class Running(plugins.TestState):
    def __init__(self, execMachines, freeText = "", briefText = "", lifecycleChange="start"):
        plugins.TestState.__init__(self, "running", freeText, briefText, started=1,
                                   executionHosts = execMachines, lifecycleChange=lifecycleChange)


class Killed(plugins.TestState):
    def __init__(self, briefText, freeText, prevState):
        plugins.TestState.__init__(self, "killed", briefText=briefText, freeText=freeText, \
                                   started=1, completed=1, executionHosts=prevState.executionHosts)
        # Cache running information, it can be useful to have this available...
        self.prevState = prevState
        self.failedPrediction = self


class RunTest(plugins.Action):
    def __init__(self):
        self.diag = logging.getLogger("run test")
        self.killDiag = logging.getLogger("kill processes")
        self.currentProcess = None
        self.killedTests = []
        self.killSignal = None
        self.lock = Lock()
    def __repr__(self):
        return "Running"
    def __call__(self, test):
        return self.runTest(test)
    def changeToRunningState(self, test):
        execMachines = test.state.executionHosts
        self.diag.info("Changing " + repr(test) + " to state Running on " + repr(execMachines))
        briefText = self.getBriefText(execMachines)
        freeText = "Running on " + ",".join(execMachines)
        newState = Running(execMachines, briefText=briefText, freeText=freeText)
        test.changeState(newState)
    def getBriefText(self, execMachinesArg):
        # Default to not bothering to print the machine name: all is local anyway
        return ""
    def runTest(self, test):
        self.describe(test)
        machine = test.app.getRunMachine()
        process = self.getTestProcess(test, machine)
        self.changeToRunningState(test)
        
        self.registerProcess(test, process)
        if test.getConfigValue("kill_timeout"):
            timer = Timer(test.getConfigValue("kill_timeout"), self.kill, (test, "timeout"))
            timer.start()
            self.wait(process)
            timer.cancel()
        else:
            self.wait(process)
        self.checkAndClear(test)
    
    def registerProcess(self, test, process):
        self.lock.acquire()
        self.currentProcess = process
        if test in self.killedTests:
            self.killProcess(test)
        self.lock.release()

    def storeReturnCode(self, test, code):
        file = open(test.makeTmpFileName("exitcode"), "w")
        file.write(str(code) + "\n")
        file.close()

    def checkAndClear(self, test):        
        returncode = self.currentProcess.returncode
        self.diag.info("Process terminated with return code " + repr(returncode))
        if os.name == "posix" and test not in self.killedTests and returncode < 0:
            # Process externally killed, but we haven't been notified. Wait for a while to see if we get kill notification
            self.waitForKill()
            
        self.lock.acquire()
        self.currentProcess = None
        if test in self.killedTests:
            self.changeToKilledState(test)
        elif returncode: # Don't bother to store return code when tests are killed, it isn't interesting
            self.storeReturnCode(test, returncode)
        
        self.lock.release()

    def waitForKill(self):
        for i in range(10):
            sleep(0.2)
            if self.killSignal is not None:
                return

    def changeToKilledState(self, test):
        self.diag.info("Killing test " + repr(test) + " in state " + test.state.category)
        briefText, fullText = self.getKillInfo(test)
        freeText = "Test " + fullText + "\n"
        test.changeState(Killed(briefText, freeText, test.state))

    def getKillInfo(self, test):
        if self.killSignal is None:
            return self.getExplicitKillInfo()
        elif self.killSignal == "timeout":
            return "TIMEOUT", "exceeded wallclock time limit of " + str(test.getConfigValue("kill_timeout")) + " seconds"
        elif self.killSignal == signal.SIGUSR1:
            return self.getUserSignalKillInfo(test, "1")
        elif self.killSignal == signal.SIGUSR2:
            return self.getUserSignalKillInfo(test, "2")
        elif self.killSignal == signal.SIGXCPU:
            return "CPULIMIT", "exceeded maximum cpu time allowed"
        elif self.killSignal == signal.SIGINT:
            return "INTERRUPT", "terminated via a keyboard interrupt (Ctrl-C)"
        else:
            briefText = "signal " + str(self.killSignal)
            return briefText, "terminated by " + briefText
        
    def getExplicitKillInfo(self):
        timeStr = plugins.localtime("%H:%M")
        return "KILLED", "killed explicitly at " + timeStr

    def getUserSignalKillInfo(self, testArg, userSignalNumber):
        return "SIGUSR" + userSignalNumber, "terminated by user signal " + userSignalNumber

    def kill(self, test, sig):
        self.lock.acquire()
        self.killedTests.append(test)
        self.killSignal = sig
        if self.currentProcess:
            self.killProcess(test)
        self.lock.release()
        
    def killProcess(self, test):
        machine = test.app.getRunMachine()
        if machine != "localhost" and test.getConfigValue("remote_shell_program") == "ssh":
            self.killRemoteProcess(test, machine)
        self.killDiag.info("Killing running test (process id " + str(self.currentProcess.pid) + ")")
        killSubProcessAndChildren(self.currentProcess, cmd=test.getConfigValue("kill_command"))

    def killRemoteProcess(self, test, machine):
        tmpDir = self.getTmpDirectory(test)
        remoteScript = os.path.join(tmpDir, "kill_test.sh")
        test.app.runCommandOn(machine, [ "sh", plugins.quote(remoteScript, '"') ])
        
    def wait(self, process):
        try:
            plugins.retryOnInterrupt(process.wait)
        except OSError: # pragma: no cover - workaround for Python bugs only
            pass # safest, as there are python bugs in this area

    def getRunDescription(self, test):
        commandArgs = self.getLocalExecuteCmdArgs(test, makeDirs=False)
        text =  "Command Line   : " + plugins.commandLineString(commandArgs) + "\n"
        interestingVars = self.getEnvironmentChanges(test)
        if len(interestingVars) == 0:
            return text
        text += "\nEnvironment variables :\n"
        for var, value in interestingVars:
            text += var + "=" + value + "\n"
        return text

    def getEnvironmentChanges(self, test):
        testEnv = test.getRunEnvironment()
        return sorted(filter(lambda (var, value): value != os.getenv(var), testEnv.items()))
        
    def getTestProcess(self, test, machine):
        commandArgs = self.getExecuteCmdArgs(test, machine)
        testEnv = test.getRunEnvironment()
        self.diag.info("Running test with args : " + repr(commandArgs))
        namingScheme = test.app.getConfigValue("filename_convention_scheme")
        stdoutStem = test.app.getStdoutName(namingScheme)
        stderrStem = test.app.getStderrName(namingScheme)
        inputStem = test.app.getStdinName(namingScheme)
        try:
            return subprocess.Popen(commandArgs, preexec_fn=self.getPreExecFunction(), \
                                    stdin=open(self.getInputFile(test, inputStem)), cwd=test.getDirectory(temporary=1), \
                                    stdout=self.makeFile(test, stdoutStem), stderr=self.makeFile(test, stderrStem), \
                                    env=testEnv, startupinfo=plugins.getProcessStartUpInfo(test.environment))
        except OSError:
            message = "OS-related error starting the test command - probably cannot find the program " + repr(commandArgs[0])
            raise plugins.TextTestError, message
        
    def getPreExecFunction(self):
        if os.name == "posix": # pragma: no cover - only run in the subprocess!
            return self.ignoreJobControlSignals

    def ignoreJobControlSignals(self): # pragma: no cover - only run in the subprocess!
        for signum in [ signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2, signal.SIGXCPU ]:
            signal.signal(signum, signal.SIG_IGN)

    def getInterpreterArgs(self, test):
        args = plugins.splitcmd(test.getConfigValue("interpreter"))
        if len(args) > 0 and args[0] == "ttpython": # interpreted to mean "whatever python TextTest runs with"
            return [ sys.executable, "-u" ] + args[1:]
        else:
            return args

    def getRemoteExecuteCmdArgs(self, test, runMachine, localArgs):
        scriptFileName = test.makeTmpFileName("run_test.sh", forComparison=0)
        scriptFile = open(scriptFileName, "w")
        scriptFile.write("#!/bin/sh\n\n")

        # Need to change working directory remotely
        tmpDir = self.getTmpDirectory(test)
        scriptFile.write("cd " + plugins.quote(tmpDir, "'") + "\n")

        for arg, value in self.getEnvironmentArgs(test): # Must set the environment remotely
            scriptFile.write("export " + arg + "=" + value + "\n")
        if test.app.getConfigValue("remote_shell_program") == "ssh":
            # SSH doesn't kill remote processes, create a kill script
            scriptFile.write('echo "kill $$" > kill_test.sh\n')
        scriptFile.write("exec " + " ".join(localArgs) + "\n")
        scriptFile.close()
        os.chmod(scriptFileName, 0775) # make executable
        remoteTmp = test.app.getRemoteTestTmpDir(test)[1]
        if remoteTmp:
            test.app.copyFileRemotely(scriptFileName, "localhost", remoteTmp, runMachine)
            remoteScript = os.path.join(remoteTmp, os.path.basename(scriptFileName))
            return test.app.getCommandArgsOn(runMachine, [ plugins.quote(remoteScript, '"') ])
        else:
            return test.app.getCommandArgsOn(runMachine, [ plugins.quote(scriptFileName, '"') ])

    def getEnvironmentArgs(self, test):
        vars = self.getEnvironmentChanges(test)
        args = []
        localTmpDir = test.app.writeDirectory
        remoteTmp = test.app.getRemoteTmpDirectory()[1]
        for var, value in vars:
            if remoteTmp:
                remoteValue = value.replace(localTmpDir, remoteTmp)
            else:
                remoteValue = value
            if var == "PATH":
                # This needs to be correctly reset remotely
                remoteValue = plugins.quote(remoteValue.replace(os.getenv(var), "${" + var + "}"), '"')
            else:
                remoteValue = plugins.quote(remoteValue, "'")
            args.append((var, remoteValue))
        return args
    
    def getTmpDirectory(self, test):
        remoteTmp = test.app.getRemoteTestTmpDir(test)[1]
        if remoteTmp:
            return remoteTmp
        else:
            return test.getDirectory(temporary=1)

    def getTimingArgs(self, test, makeDirs):
        machine, remoteTmp = test.app.getRemoteTestTmpDir(test)
        if remoteTmp:
            frameworkDir = os.path.join(remoteTmp, "framework_tmp")
            if makeDirs:
                test.app.ensureRemoteDirExists(machine, frameworkDir)
            perfFile = os.path.join(frameworkDir, "unixperf")
        else:
            perfFile = test.makeTmpFileName("unixperf", forFramework=1)
        return [ "time", "-p", "-o", perfFile ]

    def getLocalExecuteCmdArgs(self, test, makeDirs=True):
        args = []
        if test.app.hasAutomaticCputimeChecking():
            args += self.getTimingArgs(test, makeDirs)

        args += self.getInterpreterArgs(test)
        args += test.getInterpreterOptions()
        args += plugins.splitcmd(test.getConfigValue("executable"))
        args += test.getCommandLineOptions()
        return args
        
    def getExecuteCmdArgs(self, test, runMachine):
        args = self.getLocalExecuteCmdArgs(test)
        if runMachine == "localhost":
            return args
        else:
            return self.getRemoteExecuteCmdArgs(test, runMachine, args)

    def makeFile(self, test, name):
        fileName = test.makeTmpFileName(name)
        return open(fileName, "w")

    def getInputFile(self, test, inputStem):
        inputFileName = test.getFileName(inputStem)
        if inputFileName:
            return inputFileName
        else:
            return os.devnull

    def setUpSuite(self, suite):
        self.describe(suite)