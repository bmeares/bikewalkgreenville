import meerschaum as mrsm

def fetch(pipe: mrsm.Pipe, **kwargs):
    return [
            {"category":"Medical", "type":"General medical", "sqft_per_space":600},
            {"category":"Medical", "type":"Hospital", "sqft_per_space":400},
            {"category":"Medical", "type":"Medical collection", "sqft_per_space":600},
            {"category":"Medical", "type":"Medical laboratory", "sqft_per_space":600},
            {"category":"Office", "type":"General office", "sqft_per_space":600},
            {"category":"Office", "type":"Bail bond service", "sqft_per_space":600},
            {"category":"Personal service", "type":"General personal service", "sqft_per_space":500},
            {"category":"Personal service", "type":"Animal care, outdoor", "sqft_per_space":600},
            {"category":"Retail", "type":"General retail", "sqft_per_space":500},
            {"category":"Retail", "type":"Alternative financial service", "sqft_per_space":500},
            {"category":"Retail", "type":"Liquor store", "sqft_per_space":500},
            {"category":"Retail", "type":"Pawnshop", "sqft_per_space":500},
            {"category":"Retail", "type":"Sexually oriented business", "sqft_per_space":200},
            {"category":"Vehicle Sale/Service", "type":"Vehicle repair or service", "sqft_per_space":1000},
            {"category":"Vehicle Sale/Service", "type":"Vehicle sale or rental", "sqft_per_space":1000},
    ]
