"""Always-on research: competitor, SEO and social monitors that feed the Signals inbox.

Each monitor runs on a schedule (`growthcrew monitor <workspace>` or the API), reads only
through the polite fetcher or exports a person supplied, cites every finding with its URL and
date, and files what it finds as a `Signal`. A person sends a signal to the strategist or
dismisses it; nothing a monitor finds changes the strategy on its own.
"""
