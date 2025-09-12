## Extract products from recorded video file
Uses recorded video file with narration to extract products mentioned in the video.

# Prerequisites
- Python 3.11 or later
- ffmpeg

## Install dependencies

```
pip install -r requirements.txt
```


## Extract keyframes from video

```
python extract_keyframes.py --video "sample_video.mp4" --json video_metadata.json --outdir frames --match_phrases
```

## Run the app

```
streamlit run app.py
```