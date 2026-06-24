// epc_info — tiny tag-metadata dumper.
// Inventories both antennas and, for every NEW unique EPC, prints the
// raw tag metadata the CAEN reader hands back:
//   - TID + TIDLen : chip identity bytes (hex)
//   - PC           : Protocol Control word (top 5 bits = EPC length in words)
//   - XPC          : extended Protocol Control word
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>
#include <signal.h>

#include "CAENRFIDLib_Light.h"
#include "host.h"

#define MAX_TAGS      1024
#define ANTENNA_COUNT 2
#define POWER_MW      316
#define SCAN_MS       10

volatile int running = 0;

static void printHex(uint8_t *vect, uint16_t length, char *result) {
    for (int i = 0; i < length; i++) {
        sprintf(result + (i * 2), "%02X", vect[i]);
    }
    result[length * 2] = '\0';
}

static void handle_sigint(int sig) {
    (void)sig;
    running = 0;
}

int main(void) {
    CAENRFIDErrorCodes ec;
    CAENRFIDReader reader = {
        .connect       = _connect,
        .disconnect    = _disconnect,
        .tx            = _tx,
        .rx            = _rx,
        .clear_rx_data = _clear_rx_data,
        .enable_irqs   = _enable_irqs,
        .disable_irqs  = _disable_irqs
    };

    RS232_params port_params = {
        .com         = "/dev/ttyACM0",
        .baudrate    = 921600,
        .dataBits    = 8,
        .stopBits    = 1,
        .parity      = 0,
        .flowControl = 0,
    };

    signal(SIGINT, handle_sigint);

    printf("[EPC-INFO] Connecting on %s @ %d baud...\n",
           port_params.com, port_params.baudrate);
    ec = CAENRFID_Connect(&reader, CAENRFID_RS232, &port_params);
    if (ec != CAENRFID_StatusOK) {
        printf("[EPC-INFO] Failed to connect (code: %d).\n", ec);
        printf("  - Is the reader on %s? Try: sudo chmod 666 %s\n",
               port_params.com, port_params.com);
        return -1;
    }

    const char *sources[ANTENNA_COUNT] = {"Source_0", "Source_1"};
    CAENRFID_SetPower(&reader, POWER_MW);
    printf("[EPC-INFO] Power %d mW. Reading TID/PC/XPC — Ctrl+C to stop.\n\n",
           POWER_MW);

    // Ask the reader to return TID, PC and XPC alongside each EPC.
    const uint16_t flag = TID_READING | PC | XPC;

    char seen_tags[MAX_TAGS][2 * MAX_ID_LENGTH + 1];
    int  tag_count = 0;

    running = 1;
    while (running) {
        for (int a = 0; a < ANTENNA_COUNT && running; a++) {
            CAENRFIDTagList *tags = NULL, *aux;
            uint16_t numTags = 0;

            ec = CAENRFID_InventoryTag(&reader, (char *)sources[a], 0, 0, 0,
                                       NULL, 0, flag, &tags, &numTags);

            if (ec == CAENRFID_StatusOK && numTags > 0) {
                aux = tags;
                while (aux != NULL) {
                    char epcStr[2 * MAX_ID_LENGTH + 1];
                    printHex(aux->Tag.ID, aux->Tag.Length, epcStr);

                    bool is_new = true;
                    for (int i = 0; i < tag_count; i++) {
                        if (strcmp(seen_tags[i], epcStr) == 0) {
                            is_new = false;
                            break;
                        }
                    }

                    if (is_new) {
                        char tidStr[2 * MAX_TID_SIZE + 1] = "(none)";
                        if (aux->Tag.TIDLen > 0) {
                            printHex(aux->Tag.TID, aux->Tag.TIDLen, tidStr);
                        }

                        // PC word: bytes are big-endian; top 5 bits of the
                        // first byte are the EPC length in 16-bit words.
                        uint16_t pcWord  = (aux->Tag.PC[0] << 8) | aux->Tag.PC[1];
                        uint8_t  epcWords = (aux->Tag.PC[0] >> 3) & 0x1F;

                        char xpcStr[2 * XPC_LENGTH + 1];
                        printHex(aux->Tag.XPC, XPC_LENGTH, xpcStr);

                        printf("EPC : %s [%s]\n", epcStr, sources[a]);
                        printf("  TID    : %s (TIDLen=%u bytes)\n",
                               tidStr, aux->Tag.TIDLen);
                        printf("  PC     : 0x%04X (EPC length = %u words / %u bits)\n",
                               pcWord, epcWords, epcWords * 16);
                        printf("  XPC    : %s\n\n", xpcStr);
                        fflush(stdout);

                        if (tag_count < MAX_TAGS) {
                            strcpy(seen_tags[tag_count++], epcStr);
                        }
                    }

                    CAENRFIDTagList *next = aux->Next;
                    free(aux);
                    aux = next;
                }
            }
        }
        usleep(SCAN_MS * 1000);
    }

    CAENRFID_Disconnect(&reader);
    printf("\n[EPC-INFO] Disconnected. Unique tags seen: %d\n", tag_count);
    return 0;
}
