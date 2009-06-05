
import os

def findLoggerNames(fileName, keyText="Logger"):
    result = []
    for line in open(fileName).xreadlines():
        if keyText in line:
            words = line.split('"')
            for i, word in enumerate(words):
                if word not in result and i % 2 == 1: # Only take odd ones, between the quotes!
                    result.append(word)
    result.sort()
    return result

def findLoggerNamesUnder(location, **kwargs):
    result = set()
    for root, dirs, files in os.walk(location):
        for file in files:
            if file.endswith(".py"):
                fileName = os.path.join(root, file)
                result.update(findLoggerNames(fileName, **kwargs))
    return sorted(result)


class PythonLoggingGenerator:
    def __init__(self, fileName, postfix="", prefix=""):
        self.file = open(fileName, "w")
        self.postfix = "." + postfix
        self.prefix = prefix
        self.handlers = { "stdout" : "stdout" }

    def write(self, line):
        self.file.write(line + "\n")

    def parseInput(self, enabledLoggerNames, allLoggerNames):
        enabled, all = [], []
        for loggerInfo in enabledLoggerNames:
            try:
                loggerName, fileStem = loggerInfo
            except:
                loggerName = loggerInfo
                fileStem = loggerInfo
            enabled.append((loggerName, fileStem))
            all.append(loggerName)
        disabled = filter(lambda l:l not in all, allLoggerNames)
        all += disabled
        return enabled, disabled, all

    def generate(self, enabledLoggerNames=[], allLoggerNames=[], timeStdout=False):
        enabled, disabled, all = self.parseInput(enabledLoggerNames, allLoggerNames)
        self.writeHeaderSections(all, timed=timeStdout)
        if len(enabled):
            self.write("# ====== The following are enabled by default ======")
            for loggerName, fileStem in enabled:
                self.writeLoggerSection(loggerName, True, fileStem)

        if len(disabled):
            self.write("# ====== The following are disabled by default ======")
            for loggerName in disabled:
                self.writeLoggerSection(loggerName, False, loggerName)

    def writeLoggerSection(self, loggerName, enable, fileStem):
        self.write("# ======= Section for " + loggerName + " ======")
        self.write("[logger_" + loggerName + "]")
        handler = self.handlers.get(fileStem, loggerName)
        self.write("handlers=" + handler)
        self.write("qualname=" + loggerName)
        if enable:
            self.write("level=INFO\n")
        else:
            self.write("#level=INFO\n")
            
        if handler == loggerName:
            self.handlers[fileStem] = handler
            self.write("[handler_" + handler + "]")
            self.write("class=FileHandler")
            if enable:
                self.write("#formatter=timed")
            else:
                self.write("formatter=debug")
            fileName = self.prefix + fileStem.lower().replace(" ", "") + self.postfix
            if enable:
                self.write("args=('" + fileName + "', 'a')\n")
            else:
                self.write("args=('/dev/null', 'a')")
                self.write("#args=('" + fileName + "', 'a')\n")

    def writeHeaderSections(self, loggerNames, timed=False):
        loggerStr = ",".join(loggerNames)
        if timed:
            commentStr = ""
        else:
            commentStr = "#"
        self.write("""# Cruft that python logging module needs...
[loggers]
keys=root,%s

[handlers]
keys=root,stdout,%s

[formatters]
keys=timed,debug

[logger_root]
handlers=root

[handler_root]
class=StreamHandler
level=WARNING
args=(sys.stdout,)

[handler_stdout]
class=StreamHandler
args=(sys.stdout,)
%sformatter=timed

[formatter_timed]
format=%%(asctime)s - %%(message)s

[formatter_debug]
format=%%(name)s %%(levelname)s - %%(message)s
""" % (loggerStr, loggerStr, commentStr))
