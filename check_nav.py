import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from ui_server import get_dashboard

async def test():
    resp = await get_dashboard()
    html = resp.body.decode('utf-8', errors='replace')
    
    # Extract CSS
    style_start = html.find('<style>')
    style_end = html.find('</style>')
    css = html[style_start:style_end+8]
    
    # Find page-related CSS rules
    lines = css.split('\n')
    for i, line in enumerate(lines):
        if 'page' in line.lower() or 'main' in line.lower() or 'active' in line.lower():
            print(f"Line {i}: {line.rstrip()}")

asyncio.run(test())
