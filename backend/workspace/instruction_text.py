"""Remove exact repeated instruction lines, without guessing semantic equivalence."""


def merge_instructions(blocks):
    seen=set()
    lines=[]
    for block in blocks:
        if lines and block.strip():
            lines.append('')
        for line in block.splitlines():
            key=' '.join(line.split())
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            lines.append(line)
    return '\n'.join(lines).strip()
