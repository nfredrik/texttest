
import os, log4py, string, signal
from types import FileType

# Generic configuration class
class Configuration:
    def __init__(self, optionMap):
        self.optionMap = optionMap
    def getArgumentOptions(self):
        return {}
    def getSwitches(self):
        return {}
    def getActionSequence(self):
        return []
    def getFilterList(self):
        return []
    def getExecuteCommand(self, binary, test):
        return binary + " " + test.options
    def keepTmpFiles(self):
        return 0
    # Useful features that a GUI might like
    def getInteractiveActions(self):
        return []
    def printHelpText(self):
        pass
    def setUpApplication(self, app):
        pass
    
# Filter interface: all must provide these three methods
class Filter:
    def acceptsTestCase(self, test):
        return 1
    def acceptsTestSuite(self, suite):
        return 1
    def acceptsApplication(self, app):
        return 1

# Generic action to be performed: all actions need to provide these four methods
class Action:
    def __call__(self, test):
        pass
    def setUpSuite(self, suite):
        pass
    def setUpApplication(self, app):
        pass
    def getCleanUpAction(self):
        return None
    # Return a list of atomic instructions of action, test pairs that can be applied
    # Should not involve waiting or hanging on input
    def getInstructions(self, test):
        return [ (test, self) ]
    # Useful for printing in a certain format...
    def describe(self, testObj, postText = ""):
        print testObj.getIndent() + repr(self) + " " + repr(testObj) + postText
    def __repr__(self):
        return "Doing nothing on"

# Simple handle to get diagnostics object. Better than using log4py directly,
# as it ensures everything appears by default in a standard place with a standard name.
def getDiagnostics(diagName):
    return log4py.Logger().get_instance(diagName)

# Useful utility, free text input as comma-separated list which may have spaces
def commasplit(input):
    return map(string.strip, input.split(","))

# Exception to throw. It's generally good to throw this internally
class TextTestError(RuntimeError):
    pass

# Action composed of other sub-parts
class CompositeAction(Action):
    def __init__(self, subActions):
        self.subActions = subActions
    def __repr__(self):
        return "Performing " + repr(self.subActions) + " on"
    def __call__(self, test):
        for subAction in self.subActions:
            subAction(test)
    def setUpSuite(self, suite):
        for subAction in self.subActions:
            subAction.setUpSuite(suite)
    def setUpApplication(self, app):
        for subAction in self.subActions:
            subAction.setUpApplication(app)
    def getInstructions(self, test):
        instructions = []
        for subAction in self.subActions:
            instructions += subAction.getInstructions(test)
        return instructions
    def getCleanUpAction(self):
        cleanUpSubActions = []
        for subAction in self.subActions:
            cleanUp = subAction.getCleanUpAction()
            if cleanUp != None:
                cleanUpSubActions.append(cleanUp)
        if len(cleanUpSubActions):
            return CompositeAction(cleanUpSubActions)
        else:
            return None

# Action for wrapping the calls to setUpEnvironment
class SetUpEnvironment(Action):
    def __call__(self, test):
        test.setUpEnvironment()
    def setUpSuite(self, suite):
        suite.setUpEnvironment()

# Action for wrapping the calls to tearDownEnvironment
class TearDownEnvironment(Action):
    def __call__(self, test):
        test.tearDownEnvironment()
    def setUpSuite(self, suite):
        suite.tearDownEnvironment()

# Action for wrapping an executable that isn't Python, or can't be imported in the usual way
class NonPythonAction(Action):
    def __init__(self, actionText):
        self.script = os.path.abspath(actionText)
        if not os.path.isfile(self.script):
            raise TextTestError, "Could not find non-python script " + self.script
    def __repr__(self):
        return "Running script " + os.path.basename(self.script) + " for"
    def __call__(self, test):
        self.describe(test)
        self.callScript(test, "test_level")
    def setUpSuite(self, suite):
        self.describe(suite)
        os.chdir(suite.abspath)
        self.callScript(suite, "suite_level")
    def setUpApplication(self, app):
        print self, "application", app
        os.chdir(app.abspath)
        os.system(self.script + " app_level " + app.name)
    def callScript(self, test, level):
        os.system(self.script + " " + level + " " + test.name + " " + test.app.name)

# Generally useful class to encapsulate a background process, of which TextTest creates
# a few... seems it only works on UNIX right now.
class BackgroundProcess:
    def __init__(self, commandLine):
        processId = os.fork()
        if processId == 0:
            self.resetSignalHandlers()
            os.system(commandLine)
            os._exit(0)
        else:
            self.processId = processId
    def hasTerminated(self):
        try:
            procId, status = os.waitpid(self.processId, os.WNOHANG)
            return procId > 0 or status > 0
        except OSError:
            return 1
    def kill(self):
        self.killProcessAndChildren(str(self.processId))
    def killProcessAndChildren(self, pid):
        for line in os.popen("ps -efl | grep " + pid).xreadlines():
            entries = line.split()
            if entries[4] == pid:
                print "Killing child process", entries[3]
                self.killProcessAndChildren(entries[3])
        os.kill(int(pid), signal.SIGKILL)
    def resetSignalHandlers(self):
        # Set all signal handlers to default. There is a python bug
        # that processes started from threads block all signals. This
        # is very bad.
        for sigNum in range(1, signal.NSIG):
            try:
                signal.signal(sigNum, signal.SIG_DFL)
            except RuntimeError:
                # If it's out of range it's probably not very important
                pass
            
