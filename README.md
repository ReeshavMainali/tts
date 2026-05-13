# TTS Script Synthesizer

This project is a command-line utility designed to automate the process of converting multiple script text files into a single, concatenated audio file using supertonic Text-to-Speech (TTS) service. It is ideal for generating audiobooks, character narration, or dialogue recordings from written scripts.

## Features

*   **Batch Processing:** Processes all `.txt` files found within a designated `Scripts` directory.
*   **Voice Switching:** Supports dynamic voice selection within a single script by using special denoters.
*   **Voice Caching:** Efficiently manages and caches voice styles to minimize redundant API calls to the TTS service.
*   **Audio Concatenation:** Merges individual audio segments (generated for each line or voice change) into one continuous output WAV file.
*   **Clear Output:** Saves the final, combined WAV file into an `Output` directory, maintaining the original script file's base name.

## Voice Denotation and Script Structure

The system uses a specific syntax to allow control over character voices within a script file.

### 1. Default Behavior
If a line of text does not begin with a voice denoter, the system uses the `DEFAULT_VOICE` (set to `M1` by default) for the entire line.

### 2. Voice Denoters (How to Switch Voices)
To change the voice mid-script, preface the line with a voice denoter that matches the format:
`{VoiceID} Your text goes here.`

**Valid Voice IDs:**
The system supports voices formatted as `M1` through `M5` (Male) and `F1` through `F5` (Female).

**Example Script:**
If your script file contains:
```
{F3} Hello, this is the first character speaking.
{M2} And I will continue the narration now.
This line uses the last spoken voice.
```
The system will generate three distinct audio segments, one for each piece of text, using the specified voices.

## Prerequisites

1.  **Python:** Ensure you have Python 3.6+ installed.
2.  **Supertonic Library:** This project requires the `supertonic` Python library. You must install it using pip:
    ```bash
    pip install supertonic
    ```

## Setup Instructions

After cloning or placing the script in your project directory, ensure the following structure is in place:

```
.
├── text_to_speech.py      # This Python file
├── Scripts/               # Place all your .txt scripts here
│   ├── scene_01.txt
│   └── epilogue.txt
└── Output/                # Output directory (will be created automatically)
```

## How to Run the Program

1.  **Execution:** Run the main Python script from your terminal:
    ```bash
    python text_to_speech.py
    ```

### Important First Run Warning

**The very first time you run this script, it will take a significant amount of time.** This is because the underlying TTS model and necessary voice
profiles must be downloaded from the service. Please remain patient until all downloads are complete and the script reports readiness.

## Execution Flow

1.  The script first checks for the `Scripts` directory and loads all `.txt` files found within it.
2.  It initializes the TTS engine and validates voice configurations.
3.  It processes each script sequentially:
    *   It reads the text, parsing line by line to detect voice switches based on the denoter syntax.
    *   For every segment, it calls the TTS service to generate the raw audio bytes.
    *   The audio bytes are stored in memory.
    *   Once all segments of a single script are processed, they are combined into one cohesive WAV file using the `merge_wavs` function.
4.  The final, merged WAV file is saved to the `Output` directory.

## Troubleshooting

*   **"FileNotFoundError: [Errno 2] No such file or directory: 'Scripts'"**: Ensure the `Scripts` folder exists in the same directory as the script.
*   **TTS Errors**: If the script fails after the initial download phase, verify that the voice IDs used in your `.txt` files (`M1`, `F5`, etc.) are
correctly formatted and supported by the TTS model.