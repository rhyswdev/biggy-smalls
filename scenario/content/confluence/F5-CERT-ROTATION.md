# F5-CERT-ROTATION: Rotating TLS certificates on the internal F5

Space: SRE · Owner: Platform Reliability · Last updated 3 Jun 2025 by Sam Ortega

How to replace the TLS certificate and key on the internal BIG-IP for virtual servers serving names such as `*.prod.larkspur.internal`. Do this inside a change window. The old certificate stays on the device until you delete it, so rollback is quick.

## Before you start

- A signed certificate, private key and full chain for the name being replaced.
- The virtual server name and its client SSL profile, from the change ticket.
- Admin access to the BIG-IP management interface.

## Upload the new certificate and key

1. Go to System, then File Management, then SSL Certificate List.
2. Click Import. Choose Certificate as the type and name it with the year and host, for example `wildcard-prod-2026`.
3. Import the key on the Key tab under the same name. A name mismatch is the most common reason the profile update fails later, so check it before you move on.

## Update the client SSL profile

1. Go to Local Traffic, then Profiles, then SSL, then Client, and open the profile used by the virtual server.
2. Under Certificate and Key, change both to the new names.
3. Leave the chain alone unless the new certificate has a different intermediate. If it does, point the chain at the new bundle.
4. Click Update. Do not change the cipher string in the same change.

## Verify

From a host that can reach the virtual server, run:

```
openssl s_client -connect app.prod.larkspur.internal:443 -servername app.prod.larkspur.internal </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
```

Check that the subject matches the new certificate, the issuer is the expected CA and the end date is right. If a client reports an untrusted issuer, add `-showcerts` to the s_client part and compare the chain. Open the site in a browser and check each name the profile serves, not only one.

## Roll back

Set the client SSL profile back to the previous certificate and key names and click Update. The old certificate stays on the device until the change is closed, so there is no need to re-import it. After the change is closed and verified, delete the old certificate and key from SSL Certificate List.

## Who to tell

- Post in the SRE channel with the virtual server name and the new expiry date.
- Tell the owner of each service that uses the virtual server.
