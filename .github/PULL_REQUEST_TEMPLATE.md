<!--
  Plantilla de PR. Borra las secciones que no apliquen: un PR corto con dos
  secciones vacías se lee peor que uno con solo las que tienen algo que decir.
  Estos comentarios no se ven en el PR publicado.
-->

## Qué entra

<!-- Una frase de contexto y luego la tabla: qué cambia y dónde mirarlo. -->

| | Dónde |
|---|---|
| | |

## Decisiones

<!--
  La sección que más pesa. Cada punto: la decisión, la alternativa que
  descartaste y por qué. Si un cambio no tuvo alternativa, no va aquí.
-->

-

## Comprobado

<!-- Comandos reales con su resultado, no "lo he probado". -->

```
ruff check .              →
python -m pytest          →
docker compose up         →
```

## Riesgo

<!--
  El motor lo comparten varias instancias en producción: un cambio en
  backend/ las toca a todas a la vez. Di cuáles se ven afectadas, qué se
  rompe si esto está mal y cómo se vuelve atrás.
-->
