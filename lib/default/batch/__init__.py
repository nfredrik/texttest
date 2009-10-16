#!/usr/local/bin/python

import os, plugins, sys, string, time, types, shutil, datetime, testoverview, logging
from ndict import seqdict
from cPickle import Pickler

class BatchVersionFilter:
    def __init__(self, batchSession):
        self.batchSession = batchSession
    def verifyVersions(self, app):
        badVersion = self.findUnacceptableVersion(app)
        if badVersion is not None:
            raise plugins.TextTestError, "unregistered version '" + badVersion + "' for " + self.batchSession + " session."
    def findUnacceptableVersion(self, app):
        if app.getCompositeConfigValue("batch_use_version_filtering", self.batchSession) != "true":
            return
        
        allowedVersions = app.getCompositeConfigValue("batch_version", self.batchSession)
        for version in app.versions:
            if len(version) > 0 and not version in allowedVersions:
                return version
                
class BatchCategory(plugins.Filter):
    def __init__(self, state):
        self.name = state.category
        self.briefDescription, self.longDescription = state.categoryDescriptions.get(self.name, (self.name, self.name))
        self.tests = {}
        self.testSuites = []
    def addTest(self, test):
        self.tests[test.getRelPath()] = test
    def getTestLine(self, test):
        overall, postText = test.state.getTypeBreakdown()
        if postText == self.name.upper():
            # Don't double report here
            postText = ""
        elif len(postText) > 0:
            postText = " : " + postText
            return test.getIndent() + "- " + test.paddedRepr() + postText + "\n"
        return test.getIndent() + "- " + repr(test) + postText + "\n"
    def size(self):
        return len(self.tests)
    def acceptsTestCase(self, test):
        return self.tests.has_key(test.getRelPath())
    def acceptsTestSuiteContents(self, suite):
        return not suite.isEmpty()
    def describeBrief(self, app):
        if self.size() > 0:
            filters = [ self ]
            suite = app.createExtraTestSuite(filters)
            self.testSuites.append(suite)
            return "The following tests " + self.longDescription + " : \n" + \
                   self.getTestLines(suite) + "\n"
    def getTestLines(self, test):
        if test.classId() == "test-case":
            realTest = self.tests[test.getRelPath()]
            return self.getTestLine(realTest)
        else:
            lines = test.getIndent() + "In " + repr(test) + ":\n"
            for subtest in test.testcases:
                lines += self.getTestLines(subtest)
            return lines
    def getAllTests(self):
        allTests = []
        for suite in self.testSuites:
            for test in suite.testCaseList():
                allTests.append(self.tests[test.getRelPath()])
        return allTests
    def describeFull(self):
        fullDescriptionString = self.getFullDescription()
        if fullDescriptionString:
            return "\nDetailed information for the tests that " + self.longDescription + " follows...\n" + fullDescriptionString
        else:
            return ""
    def getFreeTextData(self):
        data = seqdict()
        for test in self.getAllTests():
            freeText = test.state.freeText
            if freeText:
                if not data.has_key(freeText):
                    data[freeText] = []
                data[freeText].append(test)
        return data.items()
    def testOutput(self, test):
        return repr(test) + " (under " + test.getRelPath() + ")"
    def getFullDescription(self):
        fullText = ""
        for freeText, tests in self.getFreeTextData():
            fullText += "--------------------------------------------------------" + "\n"
            if len(tests) == 1:
                test = tests[0]
                fullText += "TEST " + repr(test.state) + " " + self.testOutput(test) + "\n"
            else:
                fullText += str(len(tests)) + " TESTS " + repr(tests[0].state) + "\n"
            fullText += freeText
            if not freeText.endswith("\n"):
                fullText += "\n"
            if len(tests) > 1:
                fullText += "\n"
                for test in tests:
                    fullText += "-- " + self.testOutput(test) + "\n"
        return fullText

class BatchApplicationData:
    def __init__(self, suite):
        self.suite = suite
        self.categories = {}
        self.errorCategories = []
        self.failureCategories = []
        self.successCategories = []
    def storeCategory(self, test):
        category = test.state.category
        if not self.categories.has_key(category):
            batchCategory = BatchCategory(test.state)
            if not test.state.hasResults():
                self.errorCategories.append(batchCategory)
            elif test.state.hasSucceeded():
                self.successCategories.append(batchCategory)
            else:
                self.failureCategories.append(batchCategory)
            self.categories[category] = batchCategory
        self.categories[category].addTest(test)
    def failureCount(self):
        return self.totalTests(self.failCategories())
    def successCount(self):
        return self.totalTests(self.successCategories)
    def failCategories(self):
        return self.errorCategories + self.failureCategories
    def allCategories(self):
        return self.failCategories() + self.successCategories
    def testCount(self):
        return self.totalTests(self.allCategories())
    def totalTests(self, categoryList):
        count = 0
        for category in categoryList:
            count += category.size()
        return count
    def getFailuresBrief(self):
        contents = ""
        for category in self.failCategories():
            contents += category.describeBrief(self.suite.app)
        return contents
    def getSuccessBrief(self):
        contents = ""
        for category in self.successCategories:
            contents += category.describeBrief(self.suite.app)
        return contents
    def getDetails(self):
        contents = ""
        for category in self.allCategories():
            contents += category.describeFull()
        return contents

# Works only on UNIX
class BatchResponder(plugins.Responder):
    def __init__(self, optionMap, *args):
        self.sessionName = optionMap["b"]
        self.runId = optionMap.get("name", calculateBatchDate()) # use the command-line name if given, else the date
        self.batchAppData = seqdict()
        self.allApps = seqdict()
    def notifyComplete(self, test):
        if not self.batchAppData.has_key(test.app):
            self.addApplication(test)
        self.batchAppData[test.app].storeCategory(test)
    def getRootSuite(self, test):
        if test.parent:
            return self.getRootSuite(test.parent)
        else:
            return test
        
    def addApplication(self, test):
        rootSuite = self.getRootSuite(test)
        app = test.app
        self.batchAppData[app] = BatchApplicationData(rootSuite)
        self.allApps.setdefault(app.name, []).append(app)
        
    def notifyAllComplete(self):
        mailSender = MailSender(self.sessionName, self.runId)
        for appList in self.allApps.values():
            batchDataList = map(lambda x: self.batchAppData[x], appList)
            mailSender.send(batchDataList)

sectionHeaders = [ "Summary of all Unsuccessful tests", "Details of all Unsuccessful tests", "Summary of all Successful tests" ]

class MailSender:
    def __init__(self, sessionName, runId=""):
        self.sessionName = sessionName
        self.runId = runId
        self.diag = logging.getLogger("Mail Sender")
    def send(self, batchDataList):
        if len(batchDataList) == 0:
            self.diag.info("No responders for " + repr(app))
            return
        app = batchDataList[0].suite.app
        mailTitle = self.getMailTitle(app, batchDataList)
        mailContents = self.createMailHeaderSection(mailTitle, app, batchDataList)
        if len(batchDataList) > 1:
            for batchData in batchDataList:
                mailContents += self.getMailTitle(app, [ batchData ]) + "\n"
            mailContents += "\n"
        if not self.isAllSuccess(batchDataList):
            mailContents += self.performForAll(app, batchDataList, BatchApplicationData.getFailuresBrief, sectionHeaders[0])
            mailContents += self.performForAll(app, batchDataList, BatchApplicationData.getDetails, sectionHeaders[1])
        if not self.isAllFailure(batchDataList):
            mailContents += self.performForAll(app, batchDataList, BatchApplicationData.getSuccessBrief, sectionHeaders[2])
        self.sendOrStoreMail(app, mailContents, self.useCollection(app), self.isAllSuccess(batchDataList))
    def performForAll(self, app, batchDataList, method, headline):
        contents = headline + " follows...\n" + \
                   "---------------------------------------------------------------------------------" + "\n"
        for resp in batchDataList:
            if len(batchDataList) > 1:
                if headline.find("Details") != -1 and not resp is batchDataList[0]:
                    contents += "---------------------------------------------------------------------------------" + "\n"
                contents += self.getMailTitle(app, [ resp ]) + "\n\n"
            contents += method(resp) + "\n"
        return contents
    def storeMail(self, app, mailContents):
        localFileName = "batchreport." + app.name + app.versionSuffix()
        collFile = os.path.join(app.writeDirectory, localFileName)
        self.diag.info("Sending mail to", collFile)
        file = plugins.openForWrite(collFile)
        file.write(mailContents)
        file.close()
    def sendOrStoreMail(self, app, mailContents, useCollection=False, isAllSuccess=False):
        plugins.log.info("Creating batch report for application " + app.fullName() + " ...")
        if useCollection:
            self.storeMail(app, mailContents)
            plugins.log.info("File written.")
        else:
            if not isAllSuccess or app.getCompositeConfigValue("batch_mail_on_failure_only", self.sessionName) != "true":
                errorMessage = self.sendMail(app, mailContents)
                if errorMessage:
                    plugins.log.info("FAILED. Details follow:\n" + errorMessage.strip())
                else:
                    plugins.log.info("done.")
            else:
                plugins.log.info("not sent: all tests succeeded.")
    def exceptionOutput(self):
        exctype, value = sys.exc_info()[:2]
        from traceback import format_exception_only
        return string.join(format_exception_only(exctype, value), "")       
    def sendMail(self, app, mailContents):
        smtpServer = app.getConfigValue("smtp_server")
        fromAddress = app.getCompositeConfigValue("batch_sender", self.sessionName)
        toAddresses = plugins.commasplit(app.getCompositeConfigValue("batch_recipients", self.sessionName))
        from smtplib import SMTP
        smtp = SMTP()    
        try:
            smtp.connect(smtpServer)
        except:
            return "Could not connect to SMTP server at " + smtpServer + "\n" + self.exceptionOutput()
        try:
            smtp.sendmail(fromAddress, toAddresses, mailContents)
        except:
            return "Mail could not be sent\n" + self.exceptionOutput()
        smtp.quit()
    
    def createMailHeaderSection(self, title, app, batchDataList):
        if self.useCollection(app):
            return self.getMachineTitle(app, batchDataList) + "\n" + self.runId + "\n" + \
                   title + "\n\n" # blank line separating headers from body
        else:
            return self.createMailHeaderForSend(self.runId, title, app)
    def createMailHeaderForSend(self, runId, title, app):
        fromAddress = app.getCompositeConfigValue("batch_sender", self.sessionName)
        toAddress = app.getCompositeConfigValue("batch_recipients", self.sessionName)
        return "From: " + fromAddress + "\nTo: " + toAddress + "\n" + \
               "Subject: " + runId + " " + title + "\n\n"
    def useCollection(self, app):
        return app.getCompositeConfigValue("batch_use_collection", self.sessionName) == "true"
    def getMailHeader(self, app, batchDataList):
        versions = self.findCommonVersions(app, batchDataList)
        return app.fullName() + self.getVersionString(versions) + " : "
    def getCategoryNames(self, batchDataList):
        names = []
        for resp in batchDataList:
            for cat in resp.errorCategories:
                if not cat.name in names:
                    names.append(cat.name)
        for resp in batchDataList:
            for cat in resp.failureCategories:
                if not cat.name in names:
                    names.append(cat.name)
        for resp in batchDataList:
            for cat in resp.successCategories:
                if not cat.name in names:
                    names.append(cat.name)
        return names
    def isAllSuccess(self, batchDataList):
        return self.getTotalString(batchDataList, BatchApplicationData.failureCount) == "0"
    def isAllFailure(self, batchDataList):
        return self.getTotalString(batchDataList, BatchApplicationData.successCount) == "0"
    def getMailTitle(self, app, batchDataList):
        title = self.getMailHeader(app, batchDataList)
        title += self.getTotalString(batchDataList, BatchApplicationData.testCount) + " tests"
        if self.isAllSuccess(batchDataList):
            return title + ", all successful"
        title += " :"
        for categoryName in self.getCategoryNames(batchDataList):
            totalInCategory = self.getCategoryCount(categoryName, batchDataList)
            briefDesc = self.getBriefDescription(categoryName, batchDataList) 
            title += self.briefText(totalInCategory, briefDesc)
        # Lose trailing comma
        return title[:-1]
    def getMachineTitle(self, app, batchDataList):
        values = []
        for categoryName in self.getCategoryNames(batchDataList):
            countStr = str(self.getCategoryCount(categoryName, batchDataList))
            briefDesc = self.getBriefDescription(categoryName, batchDataList)
            values.append(briefDesc + "=" + countStr)
        return string.join(values, ',')
    def getTotalString(self, batchDataList, method):
        total = 0
        for resp in batchDataList:
            total += method(resp)
        return str(total)
    def getCategoryCount(self, categoryName, batchDataList):
        total = 0
        for resp in batchDataList:
            if resp.categories.has_key(categoryName):
                total += resp.categories[categoryName].size()
        return total
    def getBriefDescription(self, categoryName, batchDataList):
        for resp in batchDataList:
            if resp.categories.has_key(categoryName):
                return resp.categories[categoryName].briefDescription
    def getVersionString(self, versions):
        if len(versions) > 0:
            return " " + string.join(versions, ".")
        else:
            return ""
    def briefText(self, count, description):
        if count == 0 or description == "succeeded":
            return ""
        else:
            return " " + str(count) + " " + description + ","
    def findCommonVersions(self, app, batchDataList):
        if len(batchDataList) == 0:
            return app.versions
        commonVersions = []
        otherBatchData = batchDataList[1:]
        for trialVersion in batchDataList[0].suite.app.versions:
            if self.allContain(otherBatchData, trialVersion):
                commonVersions.append(trialVersion)
        return commonVersions
    def allContain(self, otherBatchData, trialVersion):
        for batchData in otherBatchData:
            if not trialVersion in batchData.suite.app.versions:
                return False
        return True

def calculateBatchDate():
    # Batch mode uses a standardised date that give a consistent answer for night-jobs.
    # Hence midnight is a bad cutover point. The day therefore starts and ends at 8am :)
    timeinseconds = plugins.globalStartTime - 8*60*60
    return time.strftime("%d%b%Y", time.localtime(timeinseconds))

def findExtraVersionParent(app, allApps):
    for parentApp in allApps:
        if app in parentApp.extras:
            return parentApp
    return app

def getVersionName(app, allApps):
    parent = findExtraVersionParent(app, allApps)
    parentVersion = parent.getFullVersion()
    fullVersion = app.getFullVersion()
    if parentVersion:
        return fullVersion
    elif fullVersion:
        return "default." + fullVersion
    else:
        return "default"
    

# Allow saving results to a historical repository
class SaveState(plugins.Responder):
    def __init__(self, optionMap, allApps):
        self.batchSession = optionMap["b"]
        self.fileName = self.createFileName(optionMap.get("name"))
        self.repositories = {}
        self.allApps = allApps
        self.diag = logging.getLogger("Save Repository")
    def createFileName(self, nameGiven):
        # include the date and the name, if any. Date is used for archiving, name for display
        parts = [ "teststate", calculateBatchDate() ]
        if nameGiven:
            parts.append(nameGiven)
        return string.join(parts, "_")
    def notifyComplete(self, test):
        if test.state.isComplete(): # might look weird but this notification also comes in scripts, e.g collecting
            test.saveState()
            if self.repositories.has_key(test.app):
                self.diag.info("Saving " + repr(test) + " to repository")
                self.saveToRepository(test)
            else:
                self.diag.info("No repositories for " + repr(test.app) + " in " + repr(self.repositories))
    def saveToRepository(self, test):
        testRepository = self.repositories[test.app]
        targetFile = os.path.join(testRepository, test.app.name, getVersionName(test.app, self.allApps), \
                                  test.getRelPath(), self.fileName)
        if os.path.isfile(targetFile):
            plugins.printWarning("File already exists at " + targetFile + " - not overwriting!")
        else:
            try:
                plugins.ensureDirExistsForFile(targetFile)
                shutil.copyfile(test.getStateFile(), targetFile)
            except IOError:
                plugins.printWarning("Could not write file at " + targetFile)
    def addSuite(self, suite):
        testStateRepository = suite.app.getCompositeConfigValue("batch_result_repository", self.batchSession)
        self.diag.info("Test state repository is " + repr(testStateRepository))
        if testStateRepository:
            self.repositories[suite.app] = os.path.abspath(testStateRepository)

class ArchiveRepository(plugins.ScriptWithArgs):
    scriptDoc = "Archive parts of the batch result repository to a history directory"
    def __init__(self, args):
        argDict = self.parseArguments(args, [ "before", "after", "session" ])
        self.descriptor = ""
        self.beforeDate = self.parseDate(argDict, "before")
        self.afterDate = self.parseDate(argDict, "after")
        self.batchSession = argDict.get("session", "default")
        self.repository = None
        if not self.beforeDate and not self.afterDate:
            raise plugins.TextTestError, "Cannot archive the entire repository - give cutoff dates!"
    def parseDate(self, dict, key):
        if not dict.has_key(key):
            return
        val = dict[key]
        self.descriptor += key + " " + val
        return self.dateInSeconds(val)
    def dateInSeconds(self, val):
        return time.mktime(time.strptime(val, "%d%b%Y"))
    def setUpApplication(self, app):
        repository = app.getCompositeConfigValue("batch_result_repository", self.batchSession)
        self.repository = os.path.join(repository, app.name)
        if not os.path.isdir(self.repository):
            raise plugins.TextTestError, "Batch result repository " + self.repository + " does not exist"
        self.archiveFilesUnder(self.repository, app)
    def archiveFilesUnder(self, repository, app):
        count = 0
        dirList = os.listdir(repository)
        dirList.sort()
        for file in dirList:
            fullPath = os.path.join(repository, file)
            if self.shouldArchive(file):
                self.archiveFile(fullPath, app)
                count += 1
            elif os.path.isdir(fullPath):
                self.archiveFilesUnder(fullPath, app)
        if count > 0:
            plugins.log.info("Archived " + str(count) + " files dated " + self.descriptor + " under " + repository.replace(self.repository + os.sep, ""))
    def archiveFile(self, fullPath, app):
        targetPath = self.getTargetPath(fullPath, app.name)
        plugins.ensureDirExistsForFile(targetPath)
        try:
            os.rename(fullPath, targetPath)
        except:
            plugins.log.info("Rename failed: " + fullPath + " " + targetPath)

    def getTargetPath(self, fullPath, appName):
        parts = fullPath.split(os.sep)
        parts.reverse()
        appIndex = parts.index(appName)
        parts[appIndex] = appName + "_history"
        parts.reverse()
        return string.join(parts, os.sep)
    def shouldArchive(self, file):
        if not file.startswith("teststate"):
            return False
        dateStr = file.split("_")[1]
        date = self.dateInSeconds(dateStr)
        if self.beforeDate and date >= self.beforeDate:
            return False
        if self.afterDate and date <= self.afterDate:
            return False
        return True

class WebPageResponder(plugins.Responder):
    def __init__(self, optionMap, allApps):
        self.batchSession = optionMap.get("b", "default")
        self.cellInfo = self.findCellInfoType(optionMap.get("coll"))
        self.diag = logging.getLogger("GenerateWebPages")
        self.allApps = allApps

    def findCellInfoType(self, collArg):
        if collArg and collArg.startswith("web."):
            return collArg[4:]
        else:
            return ""

    def addSuites(self, suites):
        # These are the ones that got through. Remove all rejected apps...
        apps = set([ suite.app for suite in suites ])
        for app in set(self.allApps).difference(apps):
            self.allApps.remove(app)
            # If the app is rejected, some of its extra versions may still not be...
            for extra in app.extras:
                if extra in apps:
                    self.allApps.append(extra)
            
    def notifyAllComplete(self):
        appInfo = self.getAppRepositoryInfo()
        plugins.log.info("Generating web pages...")
        for pageTitle, pageInfo in appInfo.items():
            plugins.log.info("Generating page for " + pageTitle)
            if len(pageInfo) == 1:
                self.generatePagePerApp(pageTitle, pageInfo)
            else:
                self.generateCommonPage(pageTitle, pageInfo)
        plugins.log.info("Completed web page generation.")

    def generatePagePerApp(self, pageTitle, pageInfo):
        for app, repository in pageInfo:
            pageTopDir = app.getCompositeConfigValue("historical_report_location", self.batchSession)
            pageDir = os.path.join(pageTopDir, app.name, self.cellInfo)
            extraVersions = self.getExtraVersions(app)
            self.diag.info("Found extra versions " + repr(extraVersions))
            relevantSubDirs = self.findRelevantSubdirectories(repository, app, extraVersions)
            self.makeAndGenerate(pageDir, app, extraVersions, relevantSubDirs, pageTitle)
                
    def getAppRepositoryInfo(self):
        appInfo = seqdict()
        for app in self.allApps:
            repository = app.getCompositeConfigValue("batch_result_repository", self.batchSession)
            if not repository:
                continue
            repository = os.path.join(repository, app.name)
            if not os.path.isdir(repository):
                plugins.printWarning("Batch result repository " + repository + " does not exist - not creating pages for " + repr(app))
                continue

            pageTitle = app.getCompositeConfigValue("historical_report_page_name", self.batchSession)
            appInfo.setdefault(pageTitle, []).append((app, repository))
        return appInfo

    def transformToCommon(self, pageInfo):
        extraVersions, relevantSubDirs = [], seqdict()
        for app, repository in pageInfo:
            extraVersions += self.getExtraVersions(app)
            relevantSubDirs.update(self.findRelevantSubdirectories(repository, app, extraVersions, self.getVersionTitle))
        return app, extraVersions, relevantSubDirs

    def getVersionTitle(self, app, version):
        title = app.fullName()
        if len(version) > 0 and version != "default":
            title += " version " + version
        return title
    
    def generateCommonPage(self, pageTitle, pageInfo):
        app, extraVersions, relevantSubDirs = self.transformToCommon(pageInfo)
        pageTopDir = app.getCompositeConfigValue("historical_report_location", self.batchSession)
        pageDir = os.path.join(pageTopDir, self.cellInfo)
        self.makeAndGenerate(pageDir, app, extraVersions, relevantSubDirs, pageTitle)
        
    def makeAndGenerate(self, pageDir, *args):
        plugins.ensureDirectoryExists(pageDir)
        try:
            self.generateWebPages(pageDir, *args)
        except:
            sys.stderr.write("Caught exception while generating web pages :\n")
            plugins.printException()

    def generateWebPages(self, pageDir, app, extraVersions, relevantSubDirs, pageTitle):
        version = getVersionName(app, self.allApps)
        generator = testoverview.GenerateWebPages(pageTitle, version, pageDir, extraVersions, app, self.cellInfo)
        subPageNames = app.getCompositeConfigValue("historical_report_subpages", self.batchSession)
        generator.generate(relevantSubDirs, subPageNames)

    def findMatchingExtraVersion(self, dirVersions, extraVersions):
        # Check all tails that this is not an extraVersion
        for pos in xrange(len(dirVersions)):
            versionToCheck = ".".join(dirVersions[pos:])
            if versionToCheck in extraVersions:
                return versionToCheck
        return ""
        
    def findRelevantSubdirectories(self, repository, app, extraVersions, versionTitleMethod=None):
        subdirs = seqdict()
        dirlist = os.listdir(repository)
        dirlist.sort()
        appVersions = set(app.versions)
        for dir in dirlist:
            dirVersions = dir.split(".")
            if set(dirVersions).issuperset(appVersions):
                currExtraVersion = self.findMatchingExtraVersion(dirVersions, extraVersions)
                if currExtraVersion:
                    version = dir.replace("." + currExtraVersion, "")
                else:
                    version = dir
                if versionTitleMethod:
                    versionTitle = versionTitleMethod(app, version)
                else:
                    versionTitle = version
                fullPath = os.path.join(repository, dir)
                self.diag.info("Found subdirectory " + dir + " with version " + versionTitle
                               + " and extra version '" + currExtraVersion + "'")
                subdirs.setdefault(versionTitle, []).append((currExtraVersion, fullPath))
        return subdirs
    
    def getExtraVersions(self, app):
        extraVersions = []
        length = len(app.versions)
        for extraApp in app.extras:
            version = ".".join(extraApp.versions[length:])
            if not version in app.versions:
                extraVersions.append(version)
        return extraVersions

    
class CollectFiles(plugins.ScriptWithArgs):
    scriptDoc = "Collect and send all batch reports that have been written to intermediate files"
    def __init__(self, args=[""]):
        argDict = self.parseArguments(args, [ "batch", "tmp" ])
        self.batchSession = argDict.get("batch", "default")
        self.mailSender = MailSender(self.batchSession)
        self.runId = "" # depends on what we pick up from collected files
        self.diag = logging.getLogger("batch collect")
        self.userName = argDict.get("tmp", "")
        if self.userName:
            plugins.log.info("Collecting batch files created by user " + self.userName + "...")
        else:
            plugins.log.info("Collecting batch files locally...")
    def setUpApplication(self, app):
        fileBodies = []
        totalValues = seqdict()
        rootDir = app.getPreviousWriteDirInfo(self.userName)
        if not os.path.isdir(rootDir):
            sys.stderr.write("No temporary directory found at " + rootDir + " - not collecting batch reports.\n")
            return
        dirlist = os.listdir(rootDir)
        dirlist.sort()
        compulsoryVersions = set(app.getCompositeConfigValue("batch_collect_compulsory_version", self.batchSession))
        versionsFound = set()
        for dir in dirlist:
            fullDir = os.path.join(rootDir, dir)
            if os.path.isdir(fullDir) and self.matchesApp(dir, app):
                currBodies, currVersions = self.parseDirectory(fullDir, app, totalValues)
                fileBodies += currBodies
                versionsFound.update(currVersions)
        if len(fileBodies) == 0:
            self.diag.info("No information found in " + rootDir)
            return

        missingVersions = compulsoryVersions.difference(versionsFound)

        mailTitle = self.getTitle(app, totalValues)
        mailContents = self.mailSender.createMailHeaderForSend(self.runId, mailTitle, app)
        mailContents += self.getBody(fileBodies, missingVersions)
        allSuccess = len(totalValues.keys()) == 1 and totalValues.keys()[0] == "succeeded"
        self.mailSender.sendOrStoreMail(app, mailContents, isAllSuccess=allSuccess)
    def matchesApp(self, dir, app):
        suffix = app.versionSuffix()
        return dir.startswith(app.name + suffix) or dir.startswith(self.batchSession + suffix)

    def parseDirectory(self, fullDir, app, totalValues):
        basicPrefix = "batchreport." + app.name
        prefix = basicPrefix + app.versionSuffix()
        # Don't collect to more collections!
        self.diag.info("Setting up application " + app.name + " looking for " + prefix) 
        filelist = os.listdir(fullDir)
        filelist.sort()
        fileBodies = []
        versionsFound = set()
        for filename in filelist:
            if filename.startswith(prefix):
                fullname = os.path.join(fullDir, filename)
                fileBody = self.parseFile(fullname, app, totalValues)
                if fileBody:
                    fileBodies.append(fileBody)
                    versionsFound.update(set(filename.replace(basicPrefix, "").split(".")))

        return fileBodies, versionsFound

    @staticmethod
    def runIsRelevant(runId, maxDays):
        try:
            runDate = datetime.date.fromtimestamp(time.mktime(time.strptime(runId, "%d%b%Y")))
        except ValueError:
            return True # Isn't necessarily a date, in which case we have no grounds for rejecting it
        todaysDate = datetime.date.today()
        timeElapsed = todaysDate - runDate
        return timeElapsed.days <= maxDays

    def parseFile(self, fullname, app, totalValues):
        localName = os.path.basename(fullname)
        plugins.log.info("Found file called " + localName)
        file = open(fullname)
        valuesLine = file.readline()
        self.runId = file.readline().strip()
        maxDays = app.getCompositeConfigValue("batch_collect_max_age_days", self.batchSession)
        if self.runIsRelevant(self.runId, maxDays):
            self.addValuesToTotal(localName, valuesLine, totalValues)
            fileBody = self.runId + " " + file.read()
            file.close()
            return fileBody
        else:
            plugins.log.info("Not including " + localName + " as run is more than " +
                             str(maxDays) + " days old (as determined by batch_collect_max_age_days).")
        
    def addValuesToTotal(self, localName, valuesLine, totalValues):
        catValues = plugins.commasplit(valuesLine.strip())
        try:
            for value in catValues:
                catName, count = value.split("=")
                if not totalValues.has_key(catName):
                    totalValues[catName] = 0
                totalValues[catName] += int(count)
        except ValueError:
            plugins.printWarning("Found truncated or old format batch report (" + localName + ") - could not parse result correctly.")
    def getTitle(self, app, totalValues):
        title = self.mailSender.getMailHeader(app, [])
        total = 0
        for value in totalValues.values():
            total += value
        title += str(total) + " tests ran"
        if len(totalValues.keys()) == 1:
            return title + ", all " + totalValues.keys()[0]
        title += " :"
        for catName, count in totalValues.items():
            title += self.mailSender.briefText(count, catName)
        # Lose trailing comma
        return title[:-1]
    def extractHeader(self, body):
        firstSep = body.find("\n") + 1
        header = body[0:firstSep]
        return header, body[firstSep:]
    def extractSection(self, sectionHeader, body):
        headerLoc = body.find(sectionHeader)
        if headerLoc == -1:
            return body.strip(), ""
        nextLine = body.find("\n", headerLoc) + 1
        if body[nextLine] == "-":
            nextLine = body.find("\n", nextLine) + 1
        section = body[0:headerLoc].strip()
        newBody = body[nextLine:].strip()
        return section, newBody
    def getBody(self, bodies, missingVersions):
        totalBody = ""
        for version in sorted(missingVersions):
            totalBody += "ERROR : No sufficiently recent run matching compulsory version '" + version + "' was found.\n"
        if len(bodies) == 1:
            return totalBody + bodies[0]

        parsedBodies = []
        for subBody in bodies:
            header, parsedSubBody = self.extractHeader(subBody)
            totalBody += header
            parsedBodies.append((header, parsedSubBody))
        totalBody += "\n"

        sectionMap = {}
        prevSectionHeader = ""
        for sectionHeader in sectionHeaders:
            parsedSections = []
            newParsedBodies = []
            for header, body in parsedBodies:
                section, newBody = self.extractSection(sectionHeader, body)
                if len(newBody) != 0:
                    newParsedBodies.append((header, newBody))
                if len(section) != 0:
                    parsedSections.append((header, section))

            totalBody += self.getSectionBody(prevSectionHeader, parsedSections)
            parsedBodies = newParsedBodies
            prevSectionHeader = sectionHeader
        totalBody += self.getSectionBody(prevSectionHeader, parsedBodies)
        return totalBody
    def getSectionBody(self, sectionHeader, parsedSections):
        if len(sectionHeader) == 0 or len(parsedSections) == 0:
            return ""
        sectionBody = sectionHeader + " follows...\n"
        detailSection = sectionHeader.find("Details") != -1
        if not detailSection or len(parsedSections) == 1: 
            sectionBody += "=================================================================================\n"
        for header, section in parsedSections:
            if len(parsedSections) > 1:
                if detailSection:
                    sectionBody += "=================================================================================\n"
                sectionBody += header + "\n"
            sectionBody += section + "\n\n"
        return sectionBody
