
class CardHelper:
    rarityorder={"Starter":10,"Common":5, "Rare":4, "Epic":3, "Legendary":2, "Unique":1}

    @classmethod
    def sort_cards_by_rarity(cls,cards):
        return sorted(cards, key=lambda x: (cls.rarityorder[x.rarity],x.name))
    @classmethod
    def sort_cards_by_rarity_with_quatity(cls,cards):
        new_collection = {}
        for card in cls.sort_cards_by_rarity(cards):
            if card.name in new_collection:
                new_collection[card.name]["quantity"] += 1
            else:
                new_collection[card.name] = {}
                new_collection[card.name]["card"] = card
                new_collection[card.name]["quantity"] = 1
        
        return [(card["card"], card["quantity"])for card in list(new_collection.values())]