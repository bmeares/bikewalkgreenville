window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, latlng, context) {
                const color = (context.hideout && context.hideout.color) || '#888888';
                return L.circleMarker(latlng, {
                    radius: 6,
                    color: color,
                    weight: 1,
                    fillColor: color,
                    fillOpacity: 0.7,
                });
            }

            ,
        function1: function(feature, layer) {
            const p = feature.properties || {};
            const lines = [];
            if (p.timestamp) {
                lines.push('<b>' + String(p.timestamp).slice(0, 10) + '</b>');
            }
            if (p.persons_killed > 0) {
                lines.push('Killed: ' + p.persons_killed);
            }
            if (p.persons_injured > 0) {
                lines.push('Injured: ' + p.persons_injured);
            }
            if (p.collision_type) {
                lines.push('Type: ' + p.collision_type);
            }
            if (p.primary_contributing_factor) {
                lines.push('Cause: ' + p.primary_contributing_factor);
            }
            if (lines.length) {
                layer.bindTooltip(lines.join('<br/>'), {
                    sticky: true
                });
            }
        }

    }
});