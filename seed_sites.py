"""Seed script to add initial sites to nouveau_rss."""
import asyncio
import sys
sys.path.insert(0, ".")

from src.rss_mcp.server import add_site


SITES = [
    # Tier 1
    ("https://www.anthropic.com/research", "Anthropic"),
    ("https://www.gleech.org/", "Gavin Leech"),
    ("https://www.interconnects.ai/", "Interconnects AI (Nathan Lambert)"),
    ("https://www.chinatalk.media/", "ChinaTalk"),
    ("https://clairebookworm.substack.com/", "cold brew blog"),
    ("https://deepmind.google/discover/blog/", "Google DeepMind"),
    ("https://andonlabs.com/blog", "Andon Labs"),
    ("https://www.apolloresearch.ai/research/", "Apollo Research"),

    # Tier 2
    ("https://blog.eladgil.com/", "Elad Blog (Elad Gil)"),
    ("https://outsidetext.substack.com/", "Outside Text (henry)"),
    ("https://helentoner.substack.com/", "Rising Tide (Helen Toner)"),
    ("https://epochai.substack.com/", "Epoch AI"),
    ("https://itcanthink.substack.com/", "It Can Think! (Chris Paxton)"),
    ("https://jeremyberman.substack.com/", "Jeremy's Substack (Jeremy Berman)"),
    ("https://learnycurve.substack.com/", "The Learning Curve (Saurabh Shah)"),
    ("https://maximelabonne.substack.com/", "Maxime Labonne"),
]


async def main():
    for url, name in SITES:
        print(f"Adding {name}...")
        result = await add_site(url, name)
        print(f"  {result}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
