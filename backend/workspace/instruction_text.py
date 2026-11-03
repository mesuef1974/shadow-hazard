"""Remove identical blocks while preserving their internal structure and order."""


def merge_instructions(blocks):
    seen=set()
    unique=[]
    for block in blocks:
        text=block.strip('\r\n')
        if not text.strip() or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return '\n\n'.join(unique)
