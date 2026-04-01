# Thrifty-X Example Pipeline
# TDOA collection and calculation workflow:
#   binary data -> carrier_detect -> .card -> detect -> .toad
#   -> identify -> .toads -> match -> .match -> tdoa -> .tdoa -> pos -> .pos
#
# For RTL-SDR capture:
#   rtl_sdr -f 162M -s 2.4M - | thriftyx capture rx0.card
# For Airspy capture:
#   thriftyx capture rx0.card --device-type airspy_mini

all: rx.toads_analysis

TOAD_FILES 		:= $(patsubst cards/%.card, toad/%.toad, $(wildcard cards/*.card))

toad/%.toad: cards/%.card detector.cfg
	@mkdir -p toad
	thriftyx detect $< -o $@

rx.toads: $(TOAD_FILES) detector.cfg
	thriftyx identify $(TOAD_FILES) -o rx.toads

rx.toads_analysis: rx.toads detector.cfg
	thriftyx analyze_toads rx.toads > rx.toads_analysis

rx.match: rx.toads detector.cfg
	thriftyx match rx.toads -o rx.match

rx.tdoa: rx.toads rx.match pos-beacon.cfg pos-rx.cfg
	thriftyx tdoa rx.toads rx.match -o rx.tdoa \
		-b pos-beacon.cfg -r pos-rx.cfg

rx.pos: rx.tdoa pos-rx.cfg
	thriftyx pos rx.tdoa -o rx.pos -r pos-rx.cfg

# Convenience targets
detect: $(TOAD_FILES)
identify: rx.toads
analyze_toads: rx.toads_analysis
match: rx.match
tdoa: rx.tdoa
pos: rx.pos

clean:
	rm -f toad/*.toad rx.toads rx.toads_analysis rx.match rx.tdoa rx.pos

.PHONY: all detect identify analyze_toads match tdoa pos clean
