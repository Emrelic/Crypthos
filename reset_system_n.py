"""System N trend state'ini sifirla — close all sonrasi kullan.

Bu script calistirildigi anda scanner bellegindeki trend yonlerini temizler.
Ancak bu SADECE scanner ayni process icinde calisirken ise yarar.

Eger program yeniden baslatilacaksa bu scripte gerek yok —
reconstruct_state_from_positions zaten acik pozisyonlardan state kurar,
pozisyon yoksa state bos kalir.

Yani: programi yeniden baslat → state otomatik temiz.
"""

print("=" * 60)
print("  System N State Reset")
print("=" * 60)
print()
print("  COZUM: Programi yeniden baslatin.")
print()
print("  Neden: System N scanner startup'ta")
print("  reconstruct_state_from_positions() cagirir.")
print("  Acik pozisyon yoksa state bos kalir.")
print()
print("  GUI'den: Ayarlar > 'RESET STATE' butonu")
print("=" * 60)
