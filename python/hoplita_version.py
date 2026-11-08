
GENERATION = 1
BUILD = "20261108-0121"


def label():
    return f"Gen {GENERATION} · {BUILD}"


def long_label():
    return f"HOPLITA {label()}"


def info():
    return {"gen": GENERATION, "date": BUILD,
            "label": label(), "long": long_label()}
