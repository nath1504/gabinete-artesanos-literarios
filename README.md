# Gabinete Digital del Taller de Artesanos Literarios

Sitio público de consulta con publicación y administración restringida a integrantes autorizados.

## Funciones públicas
- Consultar textos, archivos, imágenes, sonidos y videos.
- Buscar, filtrar y ordenar por fecha o autor.
- Ver materiales y descargar archivos.

## Funciones privadas
- Inicio de sesión para integrantes.
- Publicar, editar y eliminar materiales.
- Registrar autor y fecha.

## Publicar en Render
1. Sube el contenido de esta carpeta a un repositorio de GitHub.
2. En Render, crea un **Blueprint** y conecta el repositorio que contiene `render.yaml`.
3. Antes de crear el servicio, prepara una contraseña fuerte para `ADMIN_PASSWORD` cuando Render la solicite. El usuario inicial es `colectivo`; puedes cambiar `ADMIN_USERNAME`.
4. Render generará `SECRET_KEY` y montará un disco persistente en `/var/data` para conservar la base de datos y los archivos subidos.
5. Cuando termine el despliegue, abre la URL pública. Cualquier persona podrá consultar; solo quien tenga las credenciales podrá publicar, editar o eliminar.

## Importante
- No compartas las credenciales del colectivo.
- El disco persistente de Render y el servicio pueden tener costo según el plan vigente.
- Haz copias de seguridad periódicas del disco `/var/data`.
- La configuración inicial usa una cuenta compartida del colectivo. Para mayor trazabilidad, se puede ampliar a cuentas individuales por integrante.
- Para migrar los materiales locales existentes, copia `gabinete.db` y la carpeta `uploads` a `/var/data` después del primer despliegue, conservando sus nombres.
