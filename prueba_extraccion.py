from ollama import chat

respuesta = chat(
    model="qwen3:1.7b",
    messages=[{
        "role": "user",
        "content": """Extrae los datos principales de esta óptica:

Óptica Chile
Armazones ópticos, cristales y lentes de sol.
40,1 mil seguidores.
Tienda física y envíos a todo Chile.
Probador virtual disponible en todos los marcos.
Retiro en tienda el mismo día.
Lentes para niños y adultos.
Pago seguro.
Envíos en 48h.
Atención personalizada.
Convenios Isapre y Fonasa.
Garantía de satisfacción.
Dirección: Agustinas #1161, Oficina 202 Galería Alessandri, Santiago Centro.
Teléfono: +56 9 7126 7601
Email: contacto@opticachile.cl

Responde solamente con una lista de datos, sin explicaciones."""
    }],
    think=False
)

print(respuesta.message.content)
