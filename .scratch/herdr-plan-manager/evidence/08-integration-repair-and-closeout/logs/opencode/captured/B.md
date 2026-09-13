# B
Change only consumer.py to format legacy dollar packets with currency sign and two decimals: render({'amount': 12, 'unit': 'dollars'}) == '$12.00', likewise 0 -> '$0.00'. Verify, commit consumer.py only.
