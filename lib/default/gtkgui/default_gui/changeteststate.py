
"""
The actions in the dynamic GUI that affect the state of a test
"""

import gtk, plugins, os
from .. import guiplugins
from adminactions import ReportBugs
from default.knownbugs import CheckForBugs, BugMap
from ConfigParser import ConfigParser
from copy import copy
from threading import Thread

class BackgroundThreadHelper:
    backgroundThread = None
    def getSignalsSent(self):
        return [ "BackgroundActionCompleted" ]

    def messageAfterPerform(self):
        pass

    def performInBackground(self):
        selection = copy(self.currTestSelection)
        if self.backgroundThread and self.backgroundThread.isAlive():
            self.notify("Status", "Waiting for previous background action to finish ...")
            self.backgroundThread.join()
        BackgroundThreadHelper.backgroundThread = Thread(target=self.runBackgroundThread, args=(selection,))
        self.backgroundThread.start()
        
    def runBackgroundThread(self, *args):
        self.notify("ActionStart", False)
        errorMsg = self.performBackgroundAction(*args)
        self.notify("ActionStop", False)
        self.notify("BackgroundActionCompleted", errorMsg, self)
        
    def _runInteractive(self):
        guiplugins.ActionGUI._runInteractive(self)
        self.performInBackground()

    def notifyBackgroundActionCompleted(self, message, origAction):
        if message and origAction is self:
            self.showErrorDialog(message)
        


class SaveTests(BackgroundThreadHelper,guiplugins.ActionDialogGUI):
    defaultVersionStr = "<existing version>"
    fullVersionStr = "<full version>"
    def __init__(self, allApps, *args):
        guiplugins.ActionDialogGUI.__init__(self, allApps, *args)
        self.directAccel = None
        self.directAction = gtk.Action("Save", "_Save", \
                                       self.getDirectTooltip(), self.getStockId())
        self.directAction.connect("activate", self._respond)
        self.directAction.set_property("sensitive", False)
        self.addOption("v", "Version to save", self.defaultVersionStr)
        self.addOption("old", "Version(s) to save previous results as")
        self.addSwitch("over", "Replace successfully compared files also", 0)
        if self.hasPerformance(allApps):
            self.addSwitch("ex", "Save", 1, ["Average performance", "Exact performance"])
        # Must do this in the constructor, so that "Save" also takes account of them
        for option in self.optionGroup.options.values():
            self.addValuesFromConfig(option)

    def createDialog(self):
        dialog = guiplugins.ActionDialogGUI.createDialog(self)
        dialog.set_name("Save As")
        return dialog

    def getDialogTitle(self):
        stemsToSave = self.getStemsToSave()
        saveDesc = "Saving " + str(len(self.currTestSelection)) + " tests"
        if len(stemsToSave) > 0:
            saveDesc += ", only files " + ",".join(stemsToSave)
        return saveDesc

    def _getStockId(self):
        return "save"
    def _getTitle(self):
        return "Save _As..."
    def getTooltip(self):
        return "Save results with non-default settings"
    def getDirectTooltip(self):
        return "Save results for selected tests"
    
    def addToGroups(self, actionGroup, accelGroup):
        self.directAccel = self._addToGroups("Save", self.directAction, actionGroup, accelGroup)
        guiplugins.ActionDialogGUI.addToGroups(self, actionGroup, accelGroup)

    def setSensitivity(self, newValue):
        self._setSensitivity(self.directAction, newValue)
        self._setSensitivity(self.gtkAction, newValue)
        if newValue:
            self.updateOptions()

    def getConfirmationMessage(self):
        testsForWarn = filter(lambda test: test.stateInGui.warnOnSave(), self.currTestSelection)
        if len(testsForWarn) == 0:
            return ""
        message = "You have selected tests whose results are partial or which are registered as bugs:\n"
        for test in testsForWarn:
            message += "  Test '" + test.uniqueName + "' " + test.stateInGui.categoryRepr() + "\n"
        message += "Are you sure you want to do this?\n"
        return message

    def getSaveableTests(self, selection):
        return filter(lambda test: test.stateInGui.isSaveable(), selection)

    def updateOptions(self):
        versionOption = self.optionGroup.getOption("v")
        currOption = versionOption.defaultValue
        newVersions = self.getPossibleVersions()
        currVersions = versionOption.possibleValues
        if self.defaultVersionStr == currOption and newVersions == currVersions:
            return False
        self.optionGroup.setOptionValue("v", versionOption.defaultValue)
        self.diag.info("Setting default save version to " + self.defaultVersionStr)
        self.optionGroup.setPossibleValues("v", newVersions)
        return True
    
    def getPossibleVersions(self):
        extensions = [ self.defaultVersionStr, self.fullVersionStr ]
        for app in self.currAppSelection:
            for ext in app.getSaveableVersions():
                if not ext in extensions:
                    extensions.append(ext)
        # Include the default version always
        extensions.append("")
        return extensions
    
    def getExactness(self):
        return int(self.optionGroup.getSwitchValue("ex", 1))
        
    def isActiveOnCurrent(self, test=None, state=None):
        if state and state.isSaveable():
            return True
        for seltest in self.currTestSelection:
            if seltest is not test and seltest.stateInGui.isSaveable():
                return True
        return False

    def getStemsToSave(self):
        return [ cmp.stem for _, cmp in self.currFileSelection ]

    def getBackupVersions(self):
        versionString = self.optionGroup.getOptionValue("old")
        if versionString:
            return plugins.commasplit(versionString)
        else:
            return []

    def getVersion(self, test):
        versionString = self.optionGroup.getOptionValue("v")
        if versionString == self.fullVersionStr:
            return test.app.getFullVersion(forSave=1)
        elif versionString != self.defaultVersionStr:
            return versionString

    def performOnCurrent(self):
        backupVersions = self.getBackupVersions()
        if self.optionGroup.getOptionValue("v") in backupVersions:
            raise plugins.TextTestError, "Cannot backup to the same version we're trying to save! Choose another name."
        
    def performBackgroundAction(self, selection):
        backupVersions = self.getBackupVersions()
        stemsToSave = self.getStemsToSave()
        overwriteSuccess = self.optionGroup.getSwitchValue("over")
        tests = self.getSaveableTests(selection)
        # Calculate the versions beforehand, as saving tests can change the selection,
        # which can affect the default version calculation...
        testsWithVersions = [ (test, self.getVersion(test)) for test in tests ]
        testDesc = str(len(tests)) + " tests"
        self.notify("Status", "Saving " + testDesc + " ...")
        try:
            for test, versionString in testsWithVersions:
                testComparison = test.stateInGui
                testComparison.setObservers(self.observers)
                testComparison.save(test, self.getExactness(), versionString, overwriteSuccess, stemsToSave, backupVersions)
                newState = testComparison.makeNewState(test, "saved")
                test.changeState(newState)

            self.notify("Status", "Saved " + testDesc + ".")
        except OSError, e:
            self.notify("Status", "Failed to save " + testDesc + ".")
            errorStr = str(e)
            if "Permission" in errorStr:
                errorStr = "Failed to save " + testDesc + \
                        " : didn't have sufficient write permission to the test files"
            return errorStr


class SplitResultFiles(guiplugins.ActionGUI):
    def __init__(self, *args):
        guiplugins.ActionGUI.__init__(self, *args)
        self.latestTestCount = 0
        
    def isActiveOnCurrent(self, test=None, state=None):
        for currTest in self.currTestSelection:
            separators = currTest.getConfigValue("file_split_pattern")
            if separators:
                if currTest is test:
                    if state.isComplete():
                        return True
                elif currTest.stateInGui.isComplete():
                    return True
        return False

    def _getTitle(self):
        return "Split result files"

    def _getStockId(self):
        return "convert"

    def getTooltip(self):
        return "Split result files to be able to handle different changes separately"

    def messageAfterPerform(self):
        if self.latestTestCount == 0:
            return "No test was split."
        else:
            return "Split result files for " + plugins.pluralise(self.latestTestCount, "test") + "."
     
    def performOnCurrent(self):
        self.latestTestCount = 0
        for test in self.currTestSelection:
            separators = test.getConfigValue("file_split_pattern")
            if separators:
                self.latestTestCount += 1
                self.notify("Status", "Splitting result files for " + repr(test) + " ...")
                self.notify("ActionProgress")
                if test.stateInGui.hasResults():
                    self.splitResultFiles(test, test.stateInGui, separators)
                
    def splitResultFiles(self, test, state, separators):
        newComparisons = state.splitResultFiles(test, separators)
        if newComparisons:
            newState = state.makeNewState(test, "recalculated")
            for comp in newComparisons:
                newState.addComparison(comp)
            test.changeState(newState)


class RecomputeTests(BackgroundThreadHelper,guiplugins.ActionGUI):    
    def isActiveOnCurrent(self, test=None, state=None):
        for currTest in self.currTestSelection:
            if currTest is test:
                if state.hasStarted():
                    return True
            elif currTest.stateInGui.hasStarted():
                return True
        return False

    def _getTitle(self):
        return "Recompute Status"

    def _getStockId(self):
        return "refresh"

    def getTooltip(self):
        return "Recompute test status, including progress information if appropriate"

    def getSignalsSent(self):
        return [ "Recomputed" ] + BackgroundThreadHelper.getSignalsSent(self)
        
    def performOnCurrent(self):
        if any((test.stateInGui.isComplete() for test in self.currTestSelection)):
            for appOrTest in self.currAppSelection + self.currTestSelection:
                self.notify("Status", "Rereading configuration for " + repr(appOrTest) + " ...")
                self.notify("ActionProgress")
                appOrTest.reloadConfiguration()
        
    def performBackgroundAction(self, selection):
        latestTestCount = 0
        for test in selection:
            latestTestCount += 1
            self.notify("Status", "Recomputing status of " + repr(test) + " ...")
            test.app.recomputeProgress(test, test.stateInGui, self.observers)
            self.notify("Recomputed", test)
            
        self.notify("Status", self.getFinalMessage(latestTestCount))
            
    def getFinalMessage(self, latestTestCount):
        if latestTestCount == 0:
            return "No test needed recomputation."
        else:
            return "Recomputed status of " + plugins.pluralise(latestTestCount, "test") + "."


class MarkTest(guiplugins.ActionDialogGUI):
    def __init__(self, *args):
        guiplugins.ActionDialogGUI.__init__(self, *args)
        self.addOption("brief", "Brief text", "Checked")
        self.addOption("free", "Free text", "Checked at " + plugins.localtime())
    def _getTitle(self):
        return "_Mark"
    def getTooltip(self):
        return "Mark the selected tests"
    def performOnCurrent(self):
        for test in self.currTestSelection:
            oldState = test.stateInGui
            if oldState.isComplete():
                if test.stateInGui.isMarked():
                    oldState = test.stateInGui.oldState # Keep the old state so as not to build hierarchies ...
                newState = plugins.MarkedTestState(self.optionGroup.getOptionValue("free"),
                                                   self.optionGroup.getOptionValue("brief"), oldState)
                test.changeState(newState)
                self.notify("ActionProgress") # Just to update gui ...
    def isActiveOnCurrent(self, test=None, state=None):
        if state and state.isComplete():
            return True
        for seltest in self.currTestSelection:
            if seltest is not test and seltest.stateInGui.isComplete():
                return True
        return False

class UnmarkTest(guiplugins.ActionGUI):
    def _getTitle(self):
        return "_Unmark"
    def getTooltip(self):
        return "Unmark the selected tests"
    def performOnCurrent(self):
        for test in self.currTestSelection:
            if test.stateInGui.isMarked():
                test.stateInGui.oldState.lifecycleChange = "unmarked" # To avoid triggering completion ...
                test.changeState(test.stateInGui.oldState)
                self.notify("ActionProgress") # Just to update gui ...
    def isActiveOnCurrent(self, *args):
        for test in self.currTestSelection:
            if test.stateInGui.isMarked():
                return True
        return False

class SuspendTests(guiplugins.ActionGUI):
    def _getTitle(self):
        return "Suspend"
    def getTooltip(self):
        return "Suspend the selected tests"
    def performOnCurrent(self):
        from queuesystem.masterprocess import QueueSystemServer
        QueueSystemServer.instance.setSuspendStateForTests(self.currTestSelection, True)
    def isActiveOnCurrent(self, *args):
        return any((not test.stateInGui.isComplete() for test in self.currTestSelection))

class UnsuspendTests(guiplugins.ActionGUI):
    def _getTitle(self):
        return "Unsuspend"
    def getTooltip(self):
        return "Unsuspend the selected tests"
    def performOnCurrent(self):
        from queuesystem.masterprocess import QueueSystemServer
        QueueSystemServer.instance.setSuspendStateForTests(self.currTestSelection, False)
    def isActiveOnCurrent(self, *args):
        return any((not test.stateInGui.isComplete() for test in self.currTestSelection))



class KillTests(guiplugins.ActionGUI):
    def _getStockId(self):
        return "stop"
    def _getTitle(self):
        return "_Kill"
    def getTooltip(self):
        return "Kill selected tests"
    def isActiveOnCurrent(self, test=None, state=None):
        for seltest in self.currTestSelection:
            if seltest is test:
                if not state.isComplete():
                    return True
            else:
                if not seltest.stateInGui.isComplete():
                    return True
        return False
    def getSignalsSent(self):
        return [ "Kill" ]
    def performOnCurrent(self):
        tests = filter(lambda test: not test.stateInGui.isComplete(), self.currTestSelection)
        tests.reverse() # best to cut across the action thread rather than follow it and disturb it excessively
        testDesc = str(len(tests)) + " tests"
        self.notify("Status", "Killing " + testDesc + " ...")
        for test in tests:
            self.notify("ActionProgress")
            test.notify("Kill")

        self.notify("Status", "Killed " + testDesc + ".")
    
def applyBugsToTests(tests, bugMap):
    foundMatch = False
    for test in tests:
        newState, _ = CheckForBugs().checkTestWithBugs(test, test.stateInGui, bugMap)
        if newState:
            newState.lifecycleChange = "recalculated"
            test.changeState(newState)
            foundMatch = True
    
    return foundMatch

        
class ReportBugsAndRecompute(ReportBugs):
    def getFiles(self, *args):
        from cStringIO import StringIO
        return [ StringIO() ]
    
    def updateOptions(self):
        ReportBugs.updateOptions(self)
        multifile = int(len(self.getPossibleFileStems()) > 1)
        self.searchGroup.setOptionValue("ignore_other_errors", multifile)
        if not self.currTestSelection[0].stateInGui.hasResults():
            self.searchGroup.setSwitchValue("data_source", 2)
            
    def fillVBox(self, *args):
        ret = ReportBugs.fillVBox(self, *args)
        self.dataSourceChanged()
        return ret
            
    def getPossibleFileStems(self):
        state = self.currTestSelection[0].stateInGui
        if state.hasResults():
            return [ comp.stem for comp in state.allResults if not comp.hasSucceeded() ]
        else:
            return []
    
    def updateWithBugFile(self, bugFile, ancestors):
        bugFile.seek(0)
        bugMap = BugMap()
        bugMap.readFromFileObject(bugFile)
        if applyBugsToTests(self.currTestSelection, bugMap):
            for realFile in ReportBugs.getFiles(self, ancestors):
                realFile.write(bugFile.getvalue())
                realFile.close()
        else:
            raise plugins.TextTestError, "Information entered did not trigger on the selected test, please try again"
        
    
class FindKnownBugs(guiplugins.ActionDialogGUI):
    def __init__(self, allApps, *args):
        guiplugins.ActionDialogGUI.__init__(self, allApps, *args)
        self.optionGroup.addOption("bug", "Bug or Brief Text", allocateNofValues=2)
        self.optionGroup.addSwitch("copy", 
                                   options = [ "Apply to whole suite", "Apply to common parent suite", "Copy info into test(s)" ], 
                                   description = [ "Move the reported failure information to the root suite where it will apply to all tests", 
                                                   "Move the reported failure information to the suite where it will apply to all selected tests", 
                                                   "Copy the reported failure information to the selected tests" ]
                                   )
        self.allKnownBugFiles = []
        self.rootSuites = []
            
    def _getStockId(self):
        return "find"

    def _getTitle(self):
        return "Find Failure Information"

    def getDialogTitle(self):
        return "Find, copy and move information for automatic interpretation of test failures"
    

    def findAllBugs(self, bugMap):
        test = self.currTestSelection[0]
        bugs = CheckForBugs().findAllBugs(test, test.stateInGui, bugMap)[0]
        return [ str(b.bugInfo) for b in bugs ]
        
    def updateOptions(self):
        # Only do this on completed tests
        if not all((test.stateInGui.isComplete() for test in self.currTestSelection)):
            return False
        
        self.notify("ActionStart")
        bugMap = BugMap()
        self.rootSuites = self.findSelectedRootSuites()
        self.allKnownBugFiles = self.findAllKnownBugsFiles()
        for bugFile in self.allKnownBugFiles:
            self.notify("ActionProgress")
            bugMap.readFromFile(bugFile)
            
        # We assume the first test is representative and only check all the bugs on that one
        bugs = self.findAllBugs(bugMap)
        if bugs:
            self.optionGroup.setPossibleValues("bug", bugs)
            self.optionGroup.setValue("bug", bugs[0])
        self.notify("ActionStop")
        return False
    
    def findAllKnownBugsFiles(self):
        files = []
        def progress():
            self.notify("ActionProgress")
        for app in self.currAppSelection:
            for fileName in app.getFileNamesFromFileStructure("knownbugs", progress):
                if fileName not in files:
                    files.append(fileName)
        return files
    
    def findSelectedRootSuites(self):
        roots = []
        for test in self.currTestSelection:
            root = test.getAllTestsToRoot()[0]
            if root not in roots:
                roots.append(root)
        return roots

    def findBugFileTest(self, filePath):
        testDir = os.path.dirname(filePath)
        for suite in self.rootSuites:
            if testDir.startswith(suite.getDirectory()):
                relPath = plugins.relpath(testDir, suite.getDirectory())
                return suite.findSubtestWithPath(relPath)
    
    def getTestsToApplyTo(self, copyChoice, bugFile):
        if copyChoice == 0:
            return self.rootSuites
        elif copyChoice == 1:
            bugFileTest = self.findBugFileTest(bugFile)
            return ReportBugs.findCommonSelectedAncestors(self.currTestSelection + [ bugFileTest ])
        else:
            return self.currTestSelection

    def getFileNames(self, suitesOrTests, bugFile):
        fileNames = []
        for suiteOrTest in suitesOrTests:
            name = os.path.basename(bugFile)
            fileName = os.path.join(suiteOrTest.getDirectory(), name)
            if not any((fileName.startswith(f) for f in fileNames)):
                fileNames.append(fileName)
        
        return fileNames
    
    def findBugInfo(self, bugStr):
        for bugFile in self.allKnownBugFiles:
            parser = BugMap.makeParser(bugFile)
            for section in parser.sections():
                if (parser.has_option(section, "bug_id") and parser.get(section, "bug_id") == bugStr) or \
                   (parser.has_option(section, "brief_description") and parser.get(section, "brief_description") == bugStr):
                    newParser = ConfigParser()
                    newParser.add_section(section)
                    for key, value in parser.items(section):
                        newParser.set(section, key, value)
                    bugMap = BugMap()
                    bugMap.readFromParser(newParser)
                    if bugStr in self.findAllBugs(bugMap):
                        return bugFile, section, parser, newParser, bugMap

    def performOnCurrent(self):
        bugFile, section, parser, newParser, bugMap = self.findBugInfo(self.optionGroup.getValue("bug"))
        copyChoice = self.optionGroup.getValue("copy")
        suitesOrTests = self.getTestsToApplyTo(copyChoice, bugFile)
        newFileNames = self.getFileNames(suitesOrTests, bugFile)
        if copyChoice != 2:
            parser.remove_section(section)
            if len(parser.sections()) == 0:
                if len(newFileNames) == 1 and not os.path.isfile(newFileNames[0]):
                    self.movePath(bugFile, newFileNames[0])
                    newFileNames = [] # Don't need to write parser also
                else:
                    self.removePath(bugFile)
            else:                
                parser.write(open(bugFile, "w"))
            
        for fileName in newFileNames:
            with open(fileName, "a") as f:
                newParser.write(f)
                
        applyBugsToTests(self.currTestSelection, bugMap)
        
    @staticmethod
    def removePath(dir):
        return plugins.removePath(dir)
    
    @staticmethod
    def movePath(oldPath, newPath):
        # overridden by version control modules
        os.rename(oldPath, newPath)

        
def getInteractiveActionClasses():
    return [ SaveTests, KillTests, MarkTest, UnmarkTest, RecomputeTests, ReportBugsAndRecompute,
             SuspendTests, UnsuspendTests, SplitResultFiles, FindKnownBugs ]
 
