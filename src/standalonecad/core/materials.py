from __future__ import annotations

# Small deterministic engineering material library.  Inventor material libraries are
# proprietary/configurable, so StandaloneCAD keeps an explicit local subset and never
# pretends that an unknown name has a real physical density.
MATERIAL_DENSITY_G_CM3 = {
    'generic': 1.0,
    'steel': 7.85,
    'mild steel': 7.85,
    'carbon steel': 7.85,
    'stainless steel': 8.00,
    'aluminum': 2.70,
    'aluminium': 2.70,
    'aluminum 6061': 2.70,
    'aluminium 6061': 2.70,
    'aluminum 7075': 2.81,
    'aluminium 7075': 2.81,
    'cast iron': 7.20,
    'copper': 8.96,
    'brass': 8.50,
    'bronze': 8.80,
    'titanium': 4.51,
    'titanium ti-6al-4v': 4.43,
    'abs': 1.04,
    'pla': 1.24,
    'nylon': 1.15,
    'polycarbonate': 1.20,
    'delrin': 1.41,
    'pom': 1.41,
}

ALIASES = {
    'aisi 304': 'stainless steel',
    '304 stainless': 'stainless steel',
    'ss304': 'stainless steel',
    '6061': 'aluminum 6061',
    '7075': 'aluminum 7075',
    'ti6al4v': 'titanium ti-6al-4v',
}


def normalize_material(name: str | None) -> str:
    raw = str(name or 'Generic').strip()
    key = raw.casefold()
    key = ALIASES.get(key, key)
    for canonical in MATERIAL_DENSITY_G_CM3:
        if canonical.casefold() == key:
            return canonical.title() if canonical != 'generic' else 'Generic'
    # Preserve custom names instead of silently replacing them.
    return raw


def density_for(name: str | None) -> float | None:
    key = str(name or 'Generic').strip().casefold()
    key = ALIASES.get(key, key)
    return MATERIAL_DENSITY_G_CM3.get(key)
