
""" Action for running a test locally or on a remote machine """

import plugins, os, logging, subprocess, sys, signal, pipes
from time import sleep
from threading import Lock, Timer
from jobprocess import killSubProcessAndChildren

plugins.addCategory("killed", "killed", "were terminated before completion")

class Running(plugins.TestState):
    def __init__(self, execMachines, freeText = "", briefText = "", lifecycleChange="start"):
        plugins.TestState.__init__(self, "running", freeText, briefText, started=1,
                                   executionHosts = execMachines, lifecycleChange=lifecycleChange)

    def makeModifiedState(self, newRunStatus, newDetails, lifecycleChange):
        currRunStatus = self.briefText.split()[0]
        if newRunStatus != currRunStatus:
            currFreeTextStatus = self.freeText.splitlines()[0].rsplit(" ", 2)[0]
            newFreeText = self.freeText.replace(currFreeTextStatus, newDetails)
            newBriefText = self.briefText.replace(currRunStatus, newRunStatus)
            return self.__class__(self.executionHosts, newFreeText, newBriefText, lifecycleChange)


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
        self.currentTimer = None
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
    
    def startTimer(self, timer):
        self.currentTimer = timer
        self.currentTimer.start()

    def runMultiTimer(self, timeout, method, args):
        # Break the timer up into 5 sub-timers
        # The point is to prevent timing out too early if the process gets suspended
        subTimerCount = 5 # whatever
        subTimerTimeout = float(timeout) / subTimerCount
        timer = Timer(subTimerTimeout, method, args)
        for _ in range(subTimerCount - 1):
            timer = Timer(subTimerTimeout, self.startTimer, [ timer ])
        self.startTimer(timer)
    
    def runTest(self, test):
        self.describe(test)
        machine = test.app.getRunMachine()
        self.changeToRunningState(test)
        for postfix in self.getTestRunPostfixes(test):
            if postfix:
                test.notify("TestProcessComplete") # Checks for support processes like virtual displays, restarts if needed

            process = self.getTestProcess(test, machine, postfix)    
            self.registerProcess(test, process)
            if test.getConfigValue("kill_timeout") and not test.app.isRecording() and not test.app.isActionReplay():
                self.runMultiTimer(test.getConfigValue("kill_timeout"), self.kill, (test, "timeout"))
                self.wait(process)
                self.currentTimer.cancel()
                self.currentTimer = None
            else:
                self.wait(process)
            self.checkAndClear(test, postfix)
            
    def getTestRunPostfixes(self, test):
        postfixes = [ "" ]
        for postfix in test.getConfigValue("extra_test_process_postfix"):
            if any((test.getFileName(stem + postfix) for stem in test.defFileStems())):
                postfixes.append(postfix)
        return postfixes
    
    def registerProcess(self, test, process):
        self.lock.acquire()
        self.currentProcess = process
        if test in self.killedTests:
            self.killProcess(test)
        self.lock.release()

    def storeReturnCode(self, test, code, postfix):
        file = open(test.makeTmpFileName("exitcode" + postfix), "w")
        file.write(str(code) + "\n")
        file.close()

    def checkAndClear(self, test, postfix):        
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
            self.storeReturnCode(test, returncode, postfix)
        
        self.lock.release()

    def waitForKill(self):
        for _ in range(10):
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
        elif self.killSignal == signal.SIGXCPU:
            return "CPULIMIT", "exceeded maximum cpu time allowed"
        elif self.killSignal == signal.SIGINT:
            return "INTERRUPT", "terminated via a keyboard interrupt (Ctrl-C)"
        elif self.killSignal == signal.SIGTERM:
            return "TERMINATED", "terminated via SIGTERM signal"
        else:
            return self.getKillInfoOtherSignal(test)

    def getSignalName(self, sigNum):
        for entry in dir(signal):
            if entry.startswith("SIG") and not entry.startswith("SIG_"):
                number = getattr(signal, entry)
                if number == sigNum:
                    return entry

    def getKillInfoOtherSignal(self, test):
        briefText = self.getSignalName(self.killSignal)
        return briefText, "terminated by signal " + briefText
        
    def getExplicitKillInfo(self):
        timeStr = plugins.localtime("%H:%M")
        return "KILLED", "killed explicitly at " + timeStr

    def kill(self, test, sig):
        self.lock.acquire()
        self.killedTests.append(test)
        self.killSignal = sig
        if self.currentProcess is not None:
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
        test.app.runCommandOn(machine, [ "sh", plugins.quote(remoteScript) ])
        
    def wait(self, process):
        try:
            plugins.retryOnInterrupt(process.wait)
        except OSError: # pragma: no cover - workaround for Python bugs only
            pass # safest, as there are python bugs in this area

    def getRunDescription(self, test):
        commandArgs = self.getLocalExecuteCmdArgs(test, makeDirs=False)
        text =  "Command Line   : " + plugins.commandLineString(commandArgs) + "\n"
        text += "\nEnvironment variables :\n"
        for var, value in self.getEnvironmentChanges(test):
            text += var + "=" + value + "\n"
        return text

    def getEnvironmentChanges(self, test, postfix=""):
        testEnv = self.getTestRunEnvironment(test, postfix)
        return sorted(filter(lambda (var, value): test.app.hasChanged(var, value), testEnv.items()))
        
    def getTestRunEnvironment(self, test, postfix):
        testEnv = test.getRunEnvironment()
        if postfix and "USECASE_RECORD_SCRIPT" in testEnv:
            # Redirect usecase variables if needed
            self.fixUseCaseVariables(testEnv, postfix)
        return testEnv
    
    def fixUseCaseVariables(self, testEnv, postfix):
        for varName in [ "USECASE_RECORD_SCRIPT", "USECASE_REPLAY_SCRIPT" ]:
            if varName in testEnv:
                testEnv[varName] = testEnv.get(varName).replace("usecase", "usecase" + postfix)
        
    def getTestProcess(self, test, machine, postfix=""):
        commandArgs = self.getExecuteCmdArgs(test, machine, postfix)
        self.diag.info("Running test with args : " + repr(commandArgs))
        namingScheme = test.app.getConfigValue("filename_convention_scheme")
        stdoutStem = test.app.getStdoutName(namingScheme) + postfix
        stderrStem = test.app.getStderrName(namingScheme) + postfix
        inputStem = test.app.getStdinName(namingScheme) + postfix
        testEnv = self.getTestRunEnvironment(test, postfix)
        try:
            return subprocess.Popen(commandArgs, preexec_fn=self.getPreExecFunction(), \
                                    stdin=open(self.getInputFile(test, inputStem)), cwd=test.getDirectory(temporary=1, local=1), \
                                    stdout=self.makeFile(test, stdoutStem), stderr=self.makeFile(test, stderrStem), \
                                    env=testEnv, startupinfo=self.getProcessStartUpInfo(test))
        except OSError:
            message = "OS-related error starting the test command - probably cannot find the program " + repr(commandArgs[0])
            raise plugins.TextTestError, message

    def getProcessStartUpInfo(self, test):
        # Used for hiding the windows if we're on Windows!
        if os.name == "nt" and test.getConfigValue("use_case_record_mode") == "GUI" and \
               test.getConfigValue("virtual_display_hide_windows") == "true" and test.app.useVirtualDisplay():
            info = subprocess.STARTUPINFO()
            # Python doesn't make this easy for us: in 2.6.6 and later these flags became inaccessible
            # Alternative is to use win32api which seems excessive just for this purpose.
            winFlagModule = subprocess if hasattr(subprocess, "STARTF_USESHOWWINDOW") else subprocess._subprocess #@UndefinedVariable
            info.dwFlags |= winFlagModule.STARTF_USESHOWWINDOW
            info.wShowWindow = winFlagModule.SW_HIDE
            return info
        
    def getPreExecFunction(self):
        if os.name == "posix": # pragma: no cover - only run in the subprocess!
            return self.ignoreJobControlSignals

    def ignoreJobControlSignals(self): # pragma: no cover - only run in the subprocess!
        for signum in [ signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2, signal.SIGXCPU ]:
            signal.signal(signum, signal.SIG_IGN)

    def getInterpreterArgs(self, test, interpreter):
        args = plugins.splitcmd(interpreter)
        if len(args) > 0 and args[0] == "ttpython": # interpreted to mean "whatever python TextTest runs with"
            return [ sys.executable, "-u" ] + args[1:]
        else:
            return args
        
    def quoteLocalArg(self, arg):
        return arg if "$" in arg else pipes.quote(arg)

    def getRemoteExecuteCmdArgs(self, test, runMachine, localArgs, postfix):
        scriptFileName = test.makeTmpFileName("run_test" + postfix + ".sh", forComparison=0)
        openType = "w" if os.name == "posix" else "wb" # the 'b' is necessary so we don't get \r\n written when we just want \n
        scriptFile = open(scriptFileName, openType)
        scriptFile.write("#!/bin/sh\n\n")

        # Need to change working directory remotely
        tmpDir = self.getTmpDirectory(test)
        scriptFile.write("cd " + plugins.quote(tmpDir) + "\n")

        # Must set the environment remotely
        remoteTmp = test.app.getRemoteTmpDirectory()[1]
        for arg, value in self.getEnvironmentArgs(test, remoteTmp, postfix):
            # Two step export process for compatibility with CYGWIN and older versions of 'sh'
            scriptFile.write(arg + "=" + value + "\n")
            scriptFile.write("export " + arg + "\n")
        if test.app.getConfigValue("remote_shell_program") == "ssh":
            # SSH doesn't kill remote processes, create a kill script
            scriptFile.write('echo "kill $$" > kill_test.sh\n')
        cmdString = " ".join(map(self.quoteLocalArg, localArgs))
        if remoteTmp:
            cmdString = cmdString.replace(test.app.writeDirectory, remoteTmp)
        scriptFile.write("exec " + cmdString + "\n")
        scriptFile.close()
        os.chmod(scriptFileName, 0775) # make executable
        remoteTmp = test.app.getRemoteTestTmpDir(test)[1]
        if remoteTmp:
            test.app.copyFileRemotely(scriptFileName, "localhost", remoteTmp, runMachine)
            remoteScript = os.path.join(remoteTmp, os.path.basename(scriptFileName))
            return test.app.getCommandArgsOn(runMachine, [ plugins.quote(remoteScript) ])
        else:
            return test.app.getCommandArgsOn(runMachine, [ plugins.quote(scriptFileName) ])

    def getEnvironmentArgs(self, test, remoteTmp, postfix):
        vars = self.getEnvironmentChanges(test, postfix)
        args = []
        localTmpDir = test.app.writeDirectory
        builtinVars = [ "TEXTTEST_CHECKOUT", "TEXTTEST_CHECKOUT_NAME", "TEXTTEST_ROOT",
                        "TEXTTEST_SANDBOX", "TEXTTEST_SANDBOX_ROOT", "STORYTEXT_HOME_LOCAL" ]
        for var, value in vars:
            if var in builtinVars:
                continue
            if remoteTmp:
                remoteValue = value.replace(localTmpDir, remoteTmp)
            else:
                remoteValue = value

            currentValue = os.getenv(var)
            if currentValue:
                remoteValue = remoteValue.replace(currentValue, "${" + var + "}")
                if var == "PATH" and os.name == "nt":
                    # We assume cygwin paths, make sure we use POSIX path separators
                    remoteValue = remoteValue.replace(";", ":")
            remoteValue = plugins.quote(remoteValue)
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

    def getLocalExecuteCmdArgs(self, test, postfix="", makeDirs=True):
        args = []
        if test.app.hasAutomaticCputimeChecking():
            args += self.getTimingArgs(test, makeDirs)

        # Don't expand environment if we're running on a different file system
        expandVars = test.app.getRunMachine() == "localhost" or not test.getConfigValue("remote_copy_program")
        for interpreterName, interpreter in test.getConfigValue("interpreters", expandVars=expandVars).items():
            args += self.getInterpreterArgs(test, interpreter)
            args += test.getCommandLineOptions(stem=interpreterName + "_options")
            if postfix:
                args += test.getCommandLineOptions(stem=interpreterName + "_options" + postfix)
            if "storytext" in interpreter and test.app.useVirtualDisplay():
                # Don't display storytext editor under a virtual display!
                args.append("-x")
        args += plugins.splitcmd(test.getConfigValue("executable", expandVars=expandVars))
        args += test.getCommandLineOptions(stem="options")
        if postfix:
            args += test.getCommandLineOptions(stem="options" + postfix)
        return args
        
    def getExecuteCmdArgs(self, test, runMachine, postfix):
        args = self.getLocalExecuteCmdArgs(test, postfix)
        if runMachine == "localhost":
            return args
        else:
            return self.getRemoteExecuteCmdArgs(test, runMachine, args, postfix)

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
