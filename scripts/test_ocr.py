import pytesseract
from PIL import Image

# create a simple dummy image
img = Image.new('RGB', (200, 60), color=(73, 109, 137))
text = pytesseract.image_to_string(img)
print("OCR output:", text)
