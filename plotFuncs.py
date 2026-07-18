import matplotlib.pyplot as plt
import numpy as np

def plot(value: np.array, ylabel: str, title: str, file: str):
    fig, ax = plt.subplots()

    ax.set(xlabel="Время, c", title=title)
    ax.plot(value)
    ax.set(ylabel=ylabel)

    fig.tight_layout()
    fig.savefig(file)
    plt.close()

def plotTwin(value0: np.array, value1: np.array, ylabel0: str, ylabel1: str, title: str, file: str):
    fig, ax = plt.subplots()
    ax.set(xlabel="Время, c", title=title)

    p, = ax.plot(value0)
    ax.set(ylabel=ylabel0)
    ax.yaxis.label.set_color(p.get_color())

    tw = ax.twinx()

    tw.set(ylabel=ylabel1)
    p, = tw.plot(value1, "C1")
    tw.yaxis.label.set_color(p.get_color())

    fig.tight_layout()
    fig.savefig(file)
    plt.close()


def plotVA(cycle, file):
    plotTwin(cycle[:, 3] / 1000, np.abs(cycle[:, 4]) / 1000,
             "Напряжение, В", "Сила тока, А",
             "Напряжение и сила тока\nна батарее", file)

def plotVAIn(cycle, file):
    plotTwin(cycle[:, 0] / 1000, np.abs(cycle[:, 1]) / 1000,
             "Напряжение, В", "Сила тока, А",
             "Напряжение и сила тока\nна входе", file)

def plotPow(cycle, file):
    plot(cycle[:, 5] / 1000, "Мощность, Вт", "Мощность на батарее", file)

def plotPowIn(cycle, file):
    plot(cycle[:, 5] / 1000, "Мощность, Вт", "Мощность на входе", file)

def plotCap(cycle, file):
    pow = cycle[:, 5]
    energy = [pow[0]]
    for i in range(1, len(pow)):
        energy.append(pow[i] + energy[-1])

    plotTwin(cycle[:, 6] / 1000, np.array(energy) / 1000,
             "Ёмкость, Ач", "Энергия, Втч",
    "Ёмкость и энергия", file)

def plotTemp(cycle, file):
    plot(cycle[:, 7], "Температура, °C", "Температура зарядного устройства", file)