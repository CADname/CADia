from standalonecad.mcp.server import McpServer

def test_modern_discovery_and_tools():
    s=McpServer('inventor')
    r=s.dispatch({'jsonrpc':'2.0','id':1,'method':'server/discover','params':{'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28'}}})
    assert '2026-07-28' in r['result']['supportedVersions']
    t=s.dispatch({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})['result']['tools']
    names={x['name'] for x in t}
    assert len(names)==58 and 'inventor_extrude' in names and 'inventor_get_topology' not in names

def test_legacy_initialize():
    s=McpServer(); r=s.dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
    assert r['result']['protocolVersion']=='2025-11-25'
