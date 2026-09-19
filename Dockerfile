# DTM Mock — Railway/Docker image (faqat onlayn platforma, OpenCV'siz)
# Railway bu faylni avto-aniqlaydi va shundan quradi.
FROM python:3.12-slim

WORKDIR /app

# avval faqat requirements — kod o'zgarganda pip qatlami keshdan olinadi
COPY deploy/requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

# faqat platformaga kerakli kod (skaner/bot/testlar kirmaydi)
COPY omr_core/ omr_core/
COPY webapp/ webapp/

# data/ — Railway Volume shu yerga ulanadi (Settings -> Volumes -> /app/data),
# aks holda har deploy'da natijalar o'chib ketadi!
ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["python", "-m", "webapp.app"]
