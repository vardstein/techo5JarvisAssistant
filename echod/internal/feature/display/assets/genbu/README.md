# Genbu marks

The splash draws one of these over the wordmark, matching the screen's palette (`splash_mark.go`).

They come from the JODS Genbu generator (`genbu/main.go`, themes `obsidian`, `white`, `leafgreen`,
`sakura` and `ember`), rendered 200 pixels square with resvg and squeezed with oxipng:

    resvg -w 200 genbu-white.svg white.png
    resvg -w 200 genbu-obsidian.svg obsidian.png
    oxipng -o 4 --strip safe *.png

The SVGs are kept with the generator, not here.
