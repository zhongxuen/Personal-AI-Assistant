"""Voice provider package (§24, §25, file 10). See `app.voice.stt` and `app.voice.tts`
for the provider-agnostic contracts every concrete speech provider (a local one now,
any future cloud provider later) implements -- same split as `app.llm` for
`LLMProvider`, just with the interface and its first implementation kept in one module
per direction (STT, TTS) since each only has one implementation so far.
"""
