def getVal(val: dict) -> str:
    if val['unit'] == "string":
        return val['value']
    else:
        return str(val['value']) + " " + val['unit']

def toHTML(table):
    tab_ = ["<tr>" + ''.join([f'<th>{cell}</th>' for cell in table[0]])]
    tab_.extend(["<tr>" + ''.join([f'<td>{cell}</td>' for cell in row]) + "</tr>" for row in table[1:]])
    tab = "<table>" + ''.join(tab_) + "</table>"
    return tab


def getItemsTable(items: dict) -> str:
    table = [["Параметр", "Значение", "Параметр", "Значение"]]
    for i, (key, item) in enumerate(items.items()):
        if i % 2 == 0:
            table.append([key, getVal(item)])
        else:
            table[-1].extend([key, getVal(item)])

    return toHTML(table)