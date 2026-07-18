from parser import *
import numpy as np
from plotFuncs import *
from tableFuncs import *
import pymupdf

def err(a, b):
    return abs(a - b) / a * 100

def meanAndSpread(vals):
    mean = sum(vals) / len(vals)
    spread = max(abs(mean - i) for i in vals) * 100 / mean
    return mean, spread

def masStr(mean, spread):
    return f"{mean} &#177;{spread:.0f}%"

def masStr2(mean, spread):
    return f"{mean / 1000:.3f} &#177;{spread:.0f}%"


parser = Parser("2024-09-16_21-23-32.tsv")

items = parser.items
cycles = parser.getCycles()

##Data

#Minimum and maximum voltage
minV = items['DV']['value'] * items['Cells']['value']
maxV = items['CV']['value'] * items['Cells']['value']

#is full cycle
isFull = []

for i, cycle in enumerate(cycles):
    cycles[i] = np.array(cycle)
    for j in cycles[i]:
        j[2] = abs(j[0] * j[1]) / 1000  # Recalculate in power for more precision (mW)
        j[5] = abs(j[3] * j[4]) / 1000  # Recalculate out power for more precision (mW)
    t0 = minV if i % 2 == 0 else maxV
    t1 = minV if i % 2 == 1 else maxV

    isFull.append(err(t0, cycle[0][3]) < 10 and err(t1, cycle[-1][3]) < 2)

print(isFull)



# # Table Data

#Capacity
Caps = [i[-1][6] for i in cycles]
CapDsc = meanAndSpread(Caps[1::2])
CapChg = meanAndSpread(Caps[0::2])

#Energy
Enes = [cycles[i][-1][3] * cap / 1000 for i, cap in enumerate(Caps)]
EneDsc = meanAndSpread(Enes[1::2])
EneChg = meanAndSpread(Enes[0::2])

#Full cycle time
TimeDsc = meanAndSpread([len(cycle) for i, cycle in enumerate(cycles)
                        if i % 2 == 1 and isFull[i]])
TimeChg = meanAndSpread([len(cycle) for i, cycle in enumerate(cycles)
                        if i % 2 == 0 and isFull[i]])

#Cycles count
CycC = len(cycles)

CycDsc = CycC // 2
CycChg = CycC // 2

CycDscFull = len([1 for i, f in enumerate(isFull) if f and i % 2 == 1])
CycChgFull = len([1 for i, f in enumerate(isFull) if f and i % 2 == 0])

#Time Total
TimeTotal = parser.time

## Per cycle
CycTime = []
VStart = []
VEnd = []
Capacity = []
Energy = []

for cycle in cycles:
    CycTime.append(len(cycle))
    VStart.append(cycle[0][3])
    VEnd.append(cycle[-1][3])
    Capacity.append(cycle[-1][6])
    Energy.append(cycle[-1][6] * VEnd[-1])


print(f"CapDsc: {CapDsc[0]} : {CapDsc[1]:.0f}%")
print(f"CapChg: {CapChg[0]} : {CapChg[1]:.0f}%")

print(f"EneDsc: {EneDsc[0]} : {EneDsc[1]:.0f}%")
print(f"EneChg: {EneChg[0]} : {EneChg[1]:.0f}%")

print(f"TimeDsc: {TimeDsc[0]} : {TimeDsc[1]:.0f}%")
print(f"TimeChg: {TimeChg[0]} : {TimeChg[1]:.0f}%")

print(f"CycDsc: {CycDsc} : {CycDscFull}")
print(f"CycChg: {CycChg} : {CycChgFull}")

print(f"TimeTotal: {TimeTotal}")


# # Plots Data

# # Per cycle
aCycle = []
for i, cycle in enumerate(cycles):
    plotVA(cycle, f"plots/VA/{i}.png")
    plotPow(cycle, f"plots/Power/{i}.png")
    plotCap(cycle, f"plots/Caps/{i}.png")

    aCycle.extend(cycle)

# Full test
aCycle = np.array(aCycle)

plotVA(aCycle, "plots/full/VA.png")
plotVAIn(aCycle, "plots/full/VAIn.png")
plotPow(aCycle, "plots/full/Power.png")
plotPowIn(aCycle, "plots/full/Power.png")
plotCap(aCycle, "plots/full/Cap.png")
plotTemp(aCycle, "plots/full/Temp.png")

#Data tables
dataTable = [
    ["Параметр", "Значение", "Параметр", "Значение"],
    ["Разрядная ёмкость, Ач*", masStr2(*CapDsc), "Зарядная ёмкость, Ач*", masStr2(*CapChg)],
    ["Разрядная энергия, Втч*", masStr2(*EneDsc), "Зарядная энергия, Втч*", masStr2(*EneChg)],
    ["Время разряда, с*", masStr(*TimeDsc), "Время заряда, с*", masStr(*TimeChg)],
    ["Кол-во циклов разряда", CycDsc, "Кол-во циклов заряда", CycChg],
    ["Кол-во полных циклов разряда", CycDscFull, "Кол-во полных циклов заряда", CycChgFull],
    ["Продолжительность теста, с", TimeTotal]
]

cycTable = [["№ Ц.", "Тип", "Продолжительность, с", "Начальное напряжение, В", "Конечное напряжение, В", "Общая ёмкость, Ач", "Общая энергия, Втч"]]

for i in range(len(cycles)):
    cycTable.append([i + 1, "Полный" if isFull[i] else "Неполн",
                   CycTime[i], VStart[i], VEnd[i], Capacity[i], Energy[i]])


#HTML tables
itemTab = "<h2>Таблица Items</h2>" + getItemsTable(items)

dataTab = ("<h2>Таблица параметров батареи</h2>" + toHTML(dataTable) +
           "<p>*Значение &#177; разброс в процентах.</p>")

cycTab = ("<h1>Таблица параметров циклов</h1>" + toHTML(cycTable) +
            "</p>Далее показаны графики каждого цикла построчно.</p>")

tabCss = """
th, td {
    border-bottom: 1px solid #505050;
}

td {
    font-weight: normal;
}

th {
    background-color: #c8c8c8;
    border-bottom: 1px solid #505050;
}
"""


pdf = pymupdf.Document()

pdf.new_page()
pdf.new_page()
pdf.new_page()
pdf.new_page()

pdf[0].insert_htmlbox(pymupdf.Rect(0, 0, 250, 220), itemTab, css=tabCss)
pdf[0].insert_htmlbox(pymupdf.Rect(0, 220, 600, 480), dataTab, css=tabCss)
pdf[0].insert_htmlbox(pymupdf.Rect(0, 450, 600, 680), cycTab, css=tabCss)

for i in range(5):
    x = 0
    y = i * 150
    w = 200
    h = 200
    pdf[1].insert_image(pymupdf.Rect(x,y,x+w,y+h), filename=f"plots/VA/{i}.png")
    pdf[2].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/VA/{i+5}.png")
    x = w
    pdf[1].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/Power/{i}.png")
    pdf[2].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/Power/{i+5}.png")
    x = w * 2
    pdf[1].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/Caps/{i}.png")
    pdf[2].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/Caps/{i+5}.png")

pdf[3].insert_htmlbox(pymupdf.Rect(40, 0, 600, 220), "<h1>Общие графики</h1>", css=tabCss)

plots_ = ["Cap", "Power", "Temp", "VA", "VAIn"]

for i, im in enumerate(plots_):
    w = 280
    h = 240
    x = i % 2 * w
    y = i // 2 * h + 50
    pdf[3].insert_image(pymupdf.Rect(x, y, x + w, y + h), filename=f"plots/full/{im}.png")


pdf.save("test.pdf")
pdf.close()