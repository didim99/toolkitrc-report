import re


class Parser:
    items = []
    data = []
    endData = []
    error = []

    cycles = []

    def __init__(self, file: str):
        sect = 0
        items = []
        data = []
        endData = []

        hasHead = False

        with open(file, "r") as f:
            for line in f.readlines():
                if len(line) == "":
                    continue

                if line[0] == "=":
                    sect += 1
                    continue

                if line[-1] == "\n":
                    line = line[:-1]

                if sect == 1: #==Items==
                    items.extend(line.split())
                elif sect == 2: #==Data==
                    if hasHead:
                        data.append(line.split()[:9])
                    else:
                        hasHead = True
                elif sect == 3: #==End==
                    endData.append(line.split())
                elif sect == 4: #==Error==
                    self.error.append(line)
        self.items = self.parseItems(items)
        self.cycles = self.parseData(data)
        self.time = len(data)


    def getItems(self):
        return self.items

    def getCycles(self):
        return self.cycles

    def parseItems(self, items: list[str]):
        keys = {}
        for item in items:
            delim = item.find(":")
            key = item[:delim]

            value = item[delim + 1:]
            print(key, value)
            if value[0].isdigit():
                num = int(re.sub(r"\D*", "", value))
                unit = re.sub(r"\d*", "", value)
                keys[key] = {"value": num, "unit": unit}
            else:
                keys[key] = {"value": value, "unit": "string"}
        return keys


    def parseData(self, data):
        lastTime = -1
        cycTimes = []

        cycles = []
        cycle = []

        for line in data:
            time = self.parseTime(line[0])

            if abs(time - lastTime - 1) > 2 and time != 0:
                #print(time, lastTime)
                if lastTime != -1:
                    cycTimes.append(lastTime)
                lastTime = -1
                continue
            if time == 0:
                cycles.append(cycle.copy())
                cycle = []

            cycle.append([int(i) for i in line[1:]])
            lastTime = time

        cycles.append(cycle.copy())

        return cycles[1:]


    def parseTime(self, time: str) -> int:
        ts = [int(i) for i in time.split(":")]
        return ts[0] * 3600 + ts[1] * 60 + ts[2]
