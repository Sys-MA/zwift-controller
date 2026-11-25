from PIL import Image, ImageDraw

def make_icon(size=64):
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 4, 252, 252), radius=48, fill="#153047")
    white = "#ffffff"
    for x in (65, 191):
        d.ellipse((x-39, 164-39, x+39, 164+39), outline=white, width=9)
    d.line([(65,164),(104,87),(128,164),(65,164)], fill=white, width=9, joint="curve")
    d.line([(104,87),(166,91),(128,164),(191,164),(158,66),(180,57),(199,67),(191,83)], fill=white, width=9, joint="curve")
    d.line([(87,84),(112,84)], fill=white, width=11)
    d.ellipse((118,154,138,174), outline="#6ee7b7", width=5)
    d.line([(128,164),(140,180),(151,180)], fill="#6ee7b7", width=6)
    return im.resize((size,size), Image.Resampling.LANCZOS)

if __name__ == "__main__":
    make_icon(256).save("bike.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
