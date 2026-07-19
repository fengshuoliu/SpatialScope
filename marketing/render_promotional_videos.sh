#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/marketing/output"
CARDS_ROOT="$OUTPUT_DIR/cards"

ICON="$ROOT_DIR/docs/assets/SpatialScope-icon.png"
COMPOSITE="$ROOT_DIR/docs/figures/03-composite-overlay-preview.jpg"
NUCLEI="$ROOT_DIR/docs/figures/07-final-nuclei-segmentation.jpg"
CELL_TYPES="$ROOT_DIR/docs/figures/10-final-cell-type-assignment.jpg"
REGIONS="$ROOT_DIR/docs/figures/12-computational-roi-map.jpg"
DENSITY="$ROOT_DIR/docs/figures/16-cell-density-by-distance-band.jpg"
CARD_RENDERER="$ROOT_DIR/marketing/render_text_cards.swift"

mkdir -p "$OUTPUT_DIR" "$CARDS_ROOT"

for source in "$ICON" "$COMPOSITE" "$NUCLEI" "$CELL_TYPES" "$REGIONS" "$DENSITY" "$CARD_RENDERER"; do
    if [[ ! -f "$source" ]]; then
        printf 'Missing required source: %s\n' "$source" >&2
        exit 1
    fi
done

render_video() {
    local language="$1"
    local language_name="$2"
    local cards_dir="$CARDS_ROOT/$language"
    local output_video="$OUTPUT_DIR/SpatialScope-Promotional-Video-${language_name}-9x16.mp4"

    mkdir -p "$cards_dir"
    /usr/bin/xcrun swift "$CARD_RENDERER" "$cards_dir" "$language"

    ffmpeg -y -v warning \
    -loop 1 -framerate 30 -t 10 -i "$COMPOSITE" \
    -loop 1 -framerate 30 -t 10 -i "$ICON" \
    -loop 1 -framerate 30 -t 1.45 -i "$COMPOSITE" \
    -loop 1 -framerate 30 -t 1.45 -i "$NUCLEI" \
    -loop 1 -framerate 30 -t 1.45 -i "$CELL_TYPES" \
    -loop 1 -framerate 30 -t 1.45 -i "$REGIONS" \
    -loop 1 -framerate 30 -t 1.35 -i "$DENSITY" \
    -loop 1 -framerate 30 -t 2.35 -i "$cards_dir/intro.png" \
    -loop 1 -framerate 30 -t 1.45 -i "$cards_dir/composite.png" \
    -loop 1 -framerate 30 -t 1.45 -i "$cards_dir/nuclei.png" \
    -loop 1 -framerate 30 -t 1.45 -i "$cards_dir/cell-types.png" \
    -loop 1 -framerate 30 -t 1.45 -i "$cards_dir/regions.png" \
    -loop 1 -framerate 30 -t 1.35 -i "$cards_dir/density.png" \
    -loop 1 -framerate 30 -t 2.20 -i "$cards_dir/end.png" \
    -f lavfi -t 10 -i "aevalsrc=exprs='0.055*sin(2*PI*110*t)+0.035*sin(2*PI*164.81*t)+0.025*sin(2*PI*220*t)+0.055*sin(2*PI*660*t)*exp(-15*mod(t,1.15))|0.055*sin(2*PI*110*t+0.12)+0.035*sin(2*PI*164.81*t+0.22)+0.025*sin(2*PI*220*t+0.32)+0.055*sin(2*PI*770*t)*exp(-15*mod(t,1.15))':s=44100:c=stereo" \
    -filter_complex "
        [0:v]scale=2866:1920,crop=1080:1920,
            boxblur=34:8,eq=brightness=-0.58:contrast=1.08:saturation=0.82,
            drawbox=x=0:y=0:w=iw:h=ih:color=0x05070C@0.58:t=fill,
            trim=duration=10,setpts=PTS-STARTPTS,format=rgba[background];

        [1:v]split=4[icon_intro][icon_intro_glow][icon_brand][icon_end];
        [icon_intro]scale=390:390,format=rgba,
            fade=t=in:st=0.18:d=0.55:alpha=1,fade=t=out:st=2.02:d=0.30:alpha=1[intro_icon];
        [icon_intro_glow]scale=500:500,format=rgba,colorchannelmixer=aa=0.30,
            boxblur=28:10,fade=t=in:st=0.15:d=0.55:alpha=1,
            fade=t=out:st=2.02:d=0.30:alpha=1[intro_glow];
        [icon_brand]scale=76:76,format=rgba,fade=t=in:st=2.15:d=0.18:alpha=1,
            fade=t=out:st=7.72:d=0.20:alpha=1[brand_icon];
        [icon_end]trim=duration=2.20,scale=230:230,format=rgba,setpts=PTS+7.80/TB,
            fade=t=in:st=7.82:d=0.32:alpha=1[end_icon];

        [2:v]scale=960:644,zoompan=z='min(zoom+0.00045,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=960x644:fps=30,
            format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=1.03:d=0.12:alpha=1,
            setpts=PTS+2.20/TB[shot_composite];
        [3:v]scale=960:644,zoompan=z='min(zoom+0.00045,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=960x644:fps=30,
            format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=1.03:d=0.12:alpha=1,
            setpts=PTS+3.35/TB[shot_nuclei];
        [4:v]scale=960:644,zoompan=z='min(zoom+0.00045,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=960x644:fps=30,
            format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=1.03:d=0.12:alpha=1,
            setpts=PTS+4.50/TB[shot_celltypes];
        [5:v]scale=960:644,zoompan=z='min(zoom+0.00045,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=960x644:fps=30,
            format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=1.03:d=0.12:alpha=1,
            setpts=PTS+5.65/TB[shot_regions];
        [6:v]scale=960:644,zoompan=z='min(zoom+0.00045,1.025)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=960x644:fps=30,
            format=rgba,fade=t=in:st=0:d=0.18:alpha=1,fade=t=out:st=0.88:d=0.12:alpha=1,
            setpts=PTS+6.80/TB[shot_density];

        [7:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.24:alpha=1,
            fade=t=out:st=2.02:d=0.30:alpha=1[card_intro];
        [8:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.18:alpha=1,
            fade=t=out:st=1.03:d=0.12:alpha=1,setpts=PTS+2.20/TB[card_composite];
        [9:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.18:alpha=1,
            fade=t=out:st=1.03:d=0.12:alpha=1,setpts=PTS+3.35/TB[card_nuclei];
        [10:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.18:alpha=1,
            fade=t=out:st=1.03:d=0.12:alpha=1,setpts=PTS+4.50/TB[card_celltypes];
        [11:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.18:alpha=1,
            fade=t=out:st=1.03:d=0.12:alpha=1,setpts=PTS+5.65/TB[card_regions];
        [12:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.18:alpha=1,
            fade=t=out:st=0.88:d=0.12:alpha=1,setpts=PTS+6.80/TB[card_density];
        [13:v]scale=1080:1920,format=rgba,fade=t=in:st=0:d=0.28:alpha=1,
            setpts=PTS+7.80/TB[card_end];

        [background][intro_glow]overlay=x=(W-w)/2:y=675:eof_action=pass[s1];
        [s1][intro_icon]overlay=x=(W-w)/2:y=730:eof_action=pass[s2];
        [s2][card_intro]overlay=x=0:y=0:eof_action=pass[s3];
        [s3][card_composite]overlay=x=0:y=0:eof_action=pass[s4];
        [s4][shot_composite]overlay=x=60:y=620:eof_action=pass[s5];
        [s5][card_nuclei]overlay=x=0:y=0:eof_action=pass[s6];
        [s6][shot_nuclei]overlay=x=60:y=620:eof_action=pass[s7];
        [s7][card_celltypes]overlay=x=0:y=0:eof_action=pass[s8];
        [s8][shot_celltypes]overlay=x=60:y=620:eof_action=pass[s9];
        [s9][card_regions]overlay=x=0:y=0:eof_action=pass[s10];
        [s10][shot_regions]overlay=x=60:y=620:eof_action=pass[s11];
        [s11][card_density]overlay=x=0:y=0:eof_action=pass[s12];
        [s12][shot_density]overlay=x=60:y=620:eof_action=pass[s13];
        [s13][brand_icon]overlay=x=60:y=76:eof_action=pass[s14];
        [s14][end_icon]overlay=x=(W-w)/2:y=270:eof_action=pass[s15];
        [s15][card_end]overlay=x=0:y=0:eof_action=pass,
            fade=t=in:st=0:d=0.18,fade=t=out:st=9.72:d=0.28,
            format=yuv420p,setsar=1[video];

        [14:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS,
            highpass=f=70,lowpass=f=5000,aecho=0.82:0.45:170|340:0.16|0.08,
            volume=14dB,
            alimiter=limit=0.891:level=false:latency=true,
            afade=t=in:st=0:d=0.15,afade=t=out:st=9.35:d=0.65[audio]
    " \
    -map "[video]" -map "[audio]" -map_metadata -1 -map_chapters -1 \
    -t 10 -r 30 -c:v libx264 -profile:v high -level 4.1 -preset medium -crf 18 \
    -pix_fmt yuv420p -color_range tv -movflags +faststart -c:a aac -b:a 192k "$output_video"

    ffmpeg -y -v error -ss 8.9 -i "$output_video" -frames:v 1 \
        "$OUTPUT_DIR/SpatialScope-Promotional-Poster-${language_name}-9x16.jpg"
    ffmpeg -y -v error -i "$output_video" -vf "fps=1,scale=216:384,tile=5x2" -frames:v 1 \
        "$OUTPUT_DIR/SpatialScope-Promotional-Contact-Sheet-${language_name}.jpg"

    printf 'Created %s\n' "$output_video"
}

render_video "en" "English"
render_video "zh-Hans" "Chinese"
