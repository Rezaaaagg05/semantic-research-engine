IGNORE_CONCEPTS = [
    "Stock (firearms)",
    "Period (music)",
    "Capital (architecture)",
    "Physics",
    "Acoustics",
    "Archaeology",
    "Biology",
    "Ecology"
]


def clean_concepts(concepts):

    return [
        c
        for c in concepts
        if c not in IGNORE_CONCEPTS
    ]