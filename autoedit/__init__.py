"""AutoEdit - turn a raw clip into a post-ready short.

Pipeline: probe -> extract audio -> transcribe -> face-aware 9:16 reframe
-> word-by-word caption generation -> single-pass ffmpeg render.
"""

__version__ = "1.2.0"
