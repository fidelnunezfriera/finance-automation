# credentials/

Aquí va el JSON de la cuenta de servicio de Google, con este nombre exacto:

```
credentials/gdrive-sa.json
```

Es lo que usa el proyecto para leer y escribir en tu Google Sheet. Cómo
crearlo, paso a paso, en [SETUP.md](../SETUP.md), apartado 5.

No olvides **compartir la hoja como Editor** con el `client_email` que aparece
dentro del propio JSON. Sin eso, la cuenta de servicio existe pero no ve nada.

## Trata ese JSON como una contraseña

Quien lo tenga puede escribir en tu hoja. No lo compartas ni lo envíes por
correo o chat.

Por eso el proyecto **no debe vivir en una carpeta que sincronice OneDrive**
—Escritorio y Documentos lo están por defecto—: subiría la credencial a la
nube sin avisar. `setup.bat` lo comprueba.
